"""FastAPI 서버: 크롬 확장 프로그램이 호출하는 API."""
from contextlib import asynccontextmanager
from typing import Literal

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from . import agent, cart_builder, db, intent, profiles, recommender  # noqa: E402
from .allergens import ALLERGEN_NAMES  # noqa: E402
from .extractor import NO_LABEL_REVIEWED, extract  # noqa: E402
from .rule_engine import combine_profiles, judge  # noqa: E402


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="Kurly Allergy Care AX", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"chrome-extension://.*|https://www\.kurly\.com|http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_methods=["*"], allow_headers=["*"],
)


class LoginBody(BaseModel):
    name: str = Field(min_length=1, max_length=30)


class MemberBody(BaseModel):
    name: str = Field(min_length=1, max_length=20)
    allergens: list[str] = []
    strict: bool = False
    relation: str = ""  # 본인·아빠·엄마·아들·딸·할아버지·할머니·기타
    order: str = ""  # 첫째·둘째·셋째·막내
    aliases: list[str] = Field(default_factory=list, max_length=20)  # 집에서 부르는 이름


class AliasPreviewBody(BaseModel):
    name: str = ""
    relation: str = ""
    order: str = ""


class ChatBody(BaseModel):
    user_id: int
    thread_id: str
    message: str = Field(min_length=1, max_length=500)
    selected_member_ids: list[int] = []


class ProductBody(BaseModel):
    id: str
    name: str
    price: int | None = None
    category: str = ""
    ingredients: str = ""
    allergy_label: str = ""
    url: str = ""
    image: str = ""


class CheckBody(BaseModel):
    user_id: int
    member_ids: list[int] = []
    product: ProductBody
    save: bool = True


def _valid_allergens(allergens: list[str]) -> list[str]:
    unknown = [a for a in allergens if a not in ALLERGEN_NAMES]
    if unknown:
        raise HTTPException(422, f"관리 대상 19종에 없는 알레르겐: {unknown}")
    return allergens


@app.get("/api/health")
def health():
    return {"ok": True, "llm": intent.llm_model_name(), "llm_last_error": intent.last_error,
            "products": len(db.list_products())}


@app.get("/api/allergens")
def allergens():
    return ALLERGEN_NAMES


@app.post("/api/login")
def login(body: LoginBody):
    return db.login(body.name.strip())


@app.get("/api/users/{user_id}/members")
def list_members(user_id: int):
    return db.list_members(user_id)


def _profile_fields(body: MemberBody) -> dict:
    if body.relation and body.relation not in profiles.RELATIONS:
        raise HTTPException(422, f"관계는 다음 중 하나: {profiles.RELATIONS}")
    if body.order and body.order not in profiles.ORDERS:
        raise HTTPException(422, f"순서는 다음 중 하나: {profiles.ORDERS}")
    aliases = list(dict.fromkeys(a.strip()[:20] for a in body.aliases if a.strip()))
    return {"relation": body.relation, "order": body.order, "aliases": aliases}


@app.get("/api/profile-options")
def profile_options():
    return {"relations": profiles.RELATIONS, "orders": profiles.ORDERS}


@app.post("/api/aliases/preview")
def alias_preview(body: AliasPreviewBody):
    """프로필 화면에서 관계·순서를 고를 때 자동으로 만들어질 부르는 이름을 미리 보여 준다."""
    return profiles.auto_aliases(body.name.strip(), body.relation, body.order) if body.name.strip() else []


@app.post("/api/users/{user_id}/members")
def create_member(user_id: int, body: MemberBody):
    return db.create_member(user_id, body.name.strip(), _valid_allergens(body.allergens), body.strict,
                            **_profile_fields(body))


@app.put("/api/members/{member_id}")
def update_member(member_id: int, body: MemberBody):
    member = db.update_member(member_id, body.name.strip(), _valid_allergens(body.allergens), body.strict,
                              **_profile_fields(body))
    if member is None:
        raise HTTPException(404, "프로필을 찾을 수 없습니다")
    return member


@app.delete("/api/members/{member_id}")
def delete_member(member_id: int):
    db.delete_member(member_id)
    return {"ok": True}


@app.post("/api/chat")
def chat(body: ChatBody):
    return agent.chat(body.user_id, body.thread_id, body.message, body.selected_member_ids)


@app.post("/api/check")
def check(body: CheckBody):
    """지금 보고 있는 컬리 상품을 선택한 프로필로 판정하고, 로컬 상품 DB에 저장한다."""
    members = [m for m in db.list_members(body.user_id) if m["id"] in body.member_ids]
    combined = combine_profiles(members)
    data = body.product.model_dump()
    if not data["allergy_label"].strip():
        # 페이지에 표시가 없어도, 상품 DB에서 원물·단순 구성으로 판단해 둔 상품이면 그 판단을 쓴다 (빈 값으로 덮어쓰지 않게)
        known = db.get_product(data["id"])
        if known and known["allergy_label"] == NO_LABEL_REVIEWED:
            data["allergy_label"] = NO_LABEL_REVIEWED
    if body.save:
        product = db.upsert_product({**data, "source": "kurly-page"})
    else:
        product = {**data, **extract(data["ingredients"], data["allergy_label"])}
    verdict = judge(product, combined["avoid"], combined["strict"])
    return {**verdict, "profiles": combined["profiles"], "avoid": combined["avoid"],
            "contains": product["contains"], "cross": product["cross"]}


class MinEaBody(BaseModel):
    min_ea: int = Field(ge=1)


@app.post("/api/products/{product_id}/min-ea")
def product_min_ea(product_id: str, body: MinEaBody):
    """장바구니 담기 중 컬리 상품 페이지에서 읽은 실제 최소 구매 수량을 상품 DB에 반영한다 (가격·예산 계산용)."""
    return {"updated": db.set_min_ea(product_id, body.min_ea)}


@app.get("/api/products")
def products():
    return db.list_products()


class InteractionBody(BaseModel):
    product_id: str = Field(min_length=1, max_length=100)
    event: Literal["view", "like", "cart", "purchase"]


@app.post("/api/users/{user_id}/interactions")
def interaction(user_id: int, body: InteractionBody):
    try:
        return db.record_interaction(user_id, body.product_id, body.event)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


class RecommendationBody(BaseModel):
    user_id: int
    member_ids: list[int] = Field(default_factory=list, max_length=50)
    mode: Literal["hybrid", "item", "user", "content"] = "hybrid"
    reference_product_id: str | None = None
    keywords: list[str] = Field(default_factory=list, max_length=20)
    count: int = Field(default=5, ge=1, le=30)
    budget: int | None = Field(default=None, ge=1)
    exclude_seen: bool = True


@app.post("/api/recommendations")
def recommendations(body: RecommendationBody):
    if db.get_user(body.user_id) is None:
        raise HTTPException(404, "사용자를 찾을 수 없습니다")
    members = db.list_members(body.user_id)
    if set(body.member_ids) - {m["id"] for m in members}:
        raise HTTPException(422, "선택한 프로필이 이 사용자의 프로필이 아닙니다")
    if body.reference_product_id is not None and db.get_product(body.reference_product_id) is None:
        raise HTTPException(404, "기준 상품을 찾을 수 없습니다")
    combined = combine_profiles([m for m in members if m["id"] in body.member_ids])
    candidates, stats = cart_builder.search(body.keywords, combined["avoid"], combined["strict"])
    ranked = recommender.personalize(candidates, db.list_products(), db.list_interactions(), body.user_id,
                                     body.mode, body.reference_product_id, body.exclude_seen)
    items, notes = cart_builder.build(ranked, body.count, body.budget)
    items, validation_notes = cart_builder.revalidate(items, combined["avoid"], combined["strict"])
    return {"items": items, "mode": body.mode, "profiles": combined["profiles"], "avoid": combined["avoid"],
            "stats": stats, "notes": notes + validation_notes}
