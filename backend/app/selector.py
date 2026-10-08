"""LLM 선택: Rule Engine을 통과한 후보 안에서 상황·취향에 맞는 상품을 LLM이 고른다.

- 후보는 모두 Rule Engine PASS 상품이다. LLM은 알레르기 판정을 하지 않고, 후보 밖의 상품을 고를 수 없다.
- 새 추천(처음 요청·'다른 거 추천해줘')마다 쓴다. 후보가 개수보다 적으면 고를 게 없어 부르지 않는다.
  (10/07 전: 상황·취향이 있을 때만 써서 '짜장'처럼 단순한 요청은 늘 같은 점수 순서로 나왔다)
- 같은 대화에서 앞에서 보여 준 상품은 '(앞에서 추천함)'으로 표시하고 되도록 고르지 않게 한다.
- 개수·예산은 코드(cart_builder.build)가 다시 맞추고, 담은 뒤에는 전체를 재검증한다.
- LLM 호출이 실패하거나 쓸 만한 답이 없으면 None을 돌려주고, 호출한 쪽이 점수 순으로 고른다.
"""
from pydantic import BaseModel, Field

from . import cart_builder, intent as intent_parser

SHORTLIST = 25   # LLM에 보여 줄 후보 수 (검색 점수 상위)
WHY_LIMIT = 40   # 추천 이유 글자 수 상한


class Pick(BaseModel):
    no: int = Field(description="후보 번호")
    why: str = Field(description="이 요청·상황에 맞는 이유 한 줄 (20자 안팎). 알레르기·건강 효능은 말하지 않는다")


class Picks(BaseModel):
    """후보 중에서 고른 상품. 잘 맞는 순서대로."""

    picks: list[Pick] = Field(default_factory=list)


SYSTEM_PROMPT = """너는 마켓컬리 알레르기 맞춤 장보기 챗봇의 '상품 고르기' 담당이다.
아래 후보는 모두 알레르기 검증(Rule Engine)을 이미 통과한 상품이다.
사용자의 요청과 상황에 가장 잘 맞는 상품을 {count}개 골라 번호와 이유를 준다.

규칙:
- 반드시 후보 목록의 번호만 쓴다. 목록에 없는 상품을 만들지 않는다.
- 같은 종류만 몰리지 않게 고른다 (예: 소풍 점심이면 주먹밥만 5개가 아니라 여러 종류).
- 예산이 있으면 합계가 예산을 넘지 않게 고른다.
- '(앞에서 추천함)' 표시가 붙은 상품은 같은 대화에서 이미 보여 준 것이다. 표시 없는 후보로 개수를 채울 수 있으면 고르지 않는다.
- 이유에는 알레르기 안전·건강 효능·의학적 내용을 쓰지 않는다. 상황·요청에 맞는 점만 쓴다.
- 이 장보기의 대상은 '대상'에 적힌 사람이다. 요청 문장에 다른 사람이 나와도 이유에 그 사람을 쓰지 않는다.

대상: {profiles}
요청: {request}
상황: {situation}
취향: {preferences}
개수: {count}개 / 예산: {budget}

후보 (번호. 상품명 / 가격 / 분류):
{candidates}
"""


def should_use(situation: dict | None = None, preferences: list[str] | None = None) -> bool:
    """LLM 키가 있으면 새 추천마다 LLM이 고른다 (상황·취향은 있으면 함께 넘긴다)."""
    return bool(intent_parser.llm_models())


def _candidate_text(shortlist: list[dict]) -> str:
    return "\n".join(f"{i}. {item['name']} / {item['price']:,}원 / {item['category'].rpartition('>')[2]}"
                     + (" (앞에서 추천함)" if item.get("seen") else "")
                     for i, item in enumerate(shortlist, 1))


def select(candidates: list[dict], count: int, budget, request: str,
           situation: dict | None, preferences: list[str] | None,
           profiles: list[str] | None = None) -> tuple[list[dict], list[str], str] | None:
    """(담을 상품, 안내 문구, 고른 모델) 또는 None(점수 순으로 고르라는 뜻)."""
    from langchain_core.prompts import ChatPromptTemplate

    shortlist = candidates[:SHORTLIST]
    if len(shortlist) <= count:
        return None  # 고를 게 없으면 LLM을 부르지 않는다

    prompt = ChatPromptTemplate.from_messages([("system", SYSTEM_PROMPT), ("human", "고를 상품 번호와 이유를 줘.")])
    try:
        result, model = intent_parser.invoke_with_fallback(
            lambda llm: prompt | llm.with_structured_output(Picks),
            {"request": request, "count": count, "profiles": ", ".join(profiles or []) or "정하지 않음", "budget": cart_builder.describe_budget(budget) or "없음",
             "situation": (situation or {}).get("name") or "없음",
             "preferences": ", ".join(preferences or []) or "없음",
             "candidates": _candidate_text(shortlist)},
        )
    except Exception:
        print("[selector] LLM 선택 실패, 점수 순으로 고름")
        return None

    chosen, seen = [], set()
    for pick in result.picks:
        if 1 <= pick.no <= len(shortlist) and pick.no not in seen:   # 후보 밖 번호·중복은 버린다
            seen.add(pick.no)
            chosen.append({**shortlist[pick.no - 1], "why": pick.why.strip()[:WHY_LIMIT]})
    if not chosen:
        print("[selector] 쓸 수 있는 번호가 없어 점수 순으로 고름")
        return None

    # LLM이 고른 순서를 가장 높은 점수로 두고, 나머지 후보는 원래 점수로 둔다.
    # build가 개수를 채우고(모자라면 점수 순으로 보충) 예산을 넘으면 더 싼 후보로 바꾼다.
    top = max(item.get("score", 0) for item in candidates) + 1
    ranked = [{**item, "score": top + len(chosen) - i} for i, item in enumerate(chosen)]
    chosen_ids = {item["id"] for item in chosen}
    rest = [item for item in candidates if item["id"] not in chosen_ids]
    items, notes = cart_builder.build(ranked + rest, count, budget)
    for item in items:
        item["score"] = next((c.get("score", 0) for c in candidates if c["id"] == item["id"]), 0)
    return items, notes, model
