"""Shopping Intent Parser: 자연어 요청 → 구조화 조건.

LLM(LangChain Structured Output)이 조건만 구조화한다. 알레르기 판정은 하지 않는다.
API 키가 없거나 호출이 실패하면 규칙 기반 파서로 대체해 시연이 끊기지 않게 한다.
"""
import logging
import os
import re
from typing import Literal

from pydantic import BaseModel, Field

from .allergens import ALLERGEN_NAMES, normalize_allergen, split_allergens
from .cart_builder import PURPOSE_MAP

# langchain-google-genai가 Gemini를 부를 때마다 google-genai가 남기는 AFC(자동 함수 호출) 안내 경고.
# 우리는 그 기능을 쓰지 않고 동작에도 영향이 없어 숨긴다 (서버 창·Colab 출력이 지저분해지지 않게).
logging.getLogger("google_genai.models").setLevel(logging.ERROR)

Action = Literal["create_cart", "update_constraints", "remove_item", "replace_item",
                 "add_item", "cheaper", "question"]


class ItemGroup(BaseModel):
    """'점심이랑 과자'처럼 종류별로 따로 고를 묶음 하나."""

    label: str = Field(description="묶음 이름. 사용자가 쓴 말 그대로. 예: 점심, 과자")
    keywords: list[str] = Field(default_factory=list, description="이 묶음에서 찾을 상품 낱말. 예: ['과자']")
    count: int | None = Field(default=None, description="이 묶음에 담을 개수 (말하지 않았으면 비움)")


class ShoppingIntent(BaseModel):
    """사용자의 장보기 요청을 구조화한 조건."""

    action: Action = Field(description=(
        "create_cart: 새 장바구니 생성 / update_constraints: 기존 장바구니의 예산·개수만 변경 / "
        "remove_item: 상품 빼기 / replace_item: 상품 교체 / add_item: 상품 추가 / "
        "cheaper: 더 저렴하게 / question: 장바구니와 무관한 질문·인사"))
    target_profiles: list[str] = Field(default_factory=list, description=(
        "요청에서 사람을 가리킨 표현을 사용자가 쓴 그대로. 예: '큰애랑 아빠 저녁' → ['큰애', '아빠']. "
        "프로필 이름으로 바꾸거나 목록에 없는 사람을 빼지 않는다."))
    excluded_allergens: list[str] = Field(
        default_factory=list, description=f"요청에서 추가로 빼 달라고 한 성분. 다음 중에서만: {ALLERGEN_NAMES}")
    category_or_purpose: str | None = Field(default=None, description="상품 유형이나 목적. 예: 밀키트, 라면, 만두, 피자, 야식, 저녁")
    keywords: list[str] = Field(default_factory=list, description="찾는 상품을 가리키는 낱말. 예: 짬뽕, 떡볶이, 돈까스")
    budget: int | None = Field(default=None, description="예산 금액(원). 범위면 큰 쪽 (1~2만원 → 20000)")
    budget_mode: Literal["max", "min", "around"] | None = Field(default=None, description=(
        "금액의 뜻. max: 이내·이하·안에서·까지·으로 / min: 이상·넘게·넘는·초과 / around: 정도·쯤·가깝게·안팎·내외·전후"))
    budget_min: int | None = Field(default=None, description="범위 예산의 작은 쪽 (1~2만원 → 10000). 범위가 아니면 비운다")
    per_item: bool = Field(default=False, description=(
        "금액이 상품 하나의 가격이면 true ('만원짜리 3개', '개당 5천원 이하', '하나에 2만원'). 전체 합계면 false"))
    item_count: int | None = Field(default=None, description="담을 상품 개수")
    item_index: int | None = Field(default=None, description="'2번 상품'처럼 장바구니 번호를 가리키면 그 번호(1부터)")
    from_keyword: str | None = Field(default=None, description="빼거나 바꿀 대상 상품을 가리키는 낱말")
    to_keyword: str | None = Field(default=None, description="교체·추가할 상품을 가리키는 낱말")
    preferences: list[str] = Field(default_factory=list, description=(
        "상품 고르기에 쓸 취향 조건을 사용자가 쓴 말로. 예: 아이가 좋아할 만한, 안 매운, 든든한, 간단히 데워 먹는. "
        "이번 문장에 없으면 비운다."))
    groups: list[ItemGroup] = Field(default_factory=list, description=(
        "여러 종류를 따로 원할 때만 종류별로 나눈다. 예: '점심이랑 과자' → [{label:'점심'}, {label:'과자'}]. "
        "한 종류면 비운다."))
    situation: str | None = Field(default=None, description=(
        "이번 문장에 나온 상황. 예: 소풍, 캠핑, 야식, 손님상, 다이어트. 문장에 없으면 비운다 (추측 금지)."))
    situation_prefer: list[str] = Field(default_factory=list, description=(
        "그 상황에 어울리는 상품명 낱말 최대 8개. 예: 소풍 → 김밥, 샌드위치, 핫도그"))
    situation_avoid: list[str] = Field(default_factory=list, description=(
        "그 상황에 어울리지 않는 상품명 낱말 최대 8개. 예: 소풍 → 찌개, 전골, 라면"))


SYSTEM_PROMPT = """너는 마켓컬리 알레르기 맞춤 장보기 챗봇의 '조건 구조화' 담당이다.
사용자의 말을 ShoppingIntent 구조로만 바꾼다.

규칙:
- 어떤 상품이 알레르기에 안전한지는 절대 판단하지 않는다. 판정은 Rule Engine이 한다.
- target_profiles에는 요청에서 사람을 가리킨 표현을 사용자가 쓴 그대로 넣는다 (예: '큰애', '아들', '남편').
  아래 프로필 목록에 없는 사람이라도 빼지 말고, 다른 이름으로 바꿔 넣지도 않는다. 사람을 언급하지 않았으면 비운다.
  (누가 어느 프로필인지는 서비스가 별칭 표로 따로 확인한다.)
- excluded_allergens에는 사용자가 이번 요청에서 직접 빼 달라고 한 성분만 표준명으로 넣는다.
- 금액은 원 단위 정수로 바꾼다 (2만원 → 20000). '2만원 넘게'는 budget_mode=min, '2만원에 가깝게'는 around,
  '1~2만원'은 budget=20000·budget_min=10000, '만원짜리 3개'는 per_item=true다.
  per_item은 '짜리·개당·하나에·한 개에'가 있을 때만 true다. '3개 5천원으로', '5개 3만원 안에서'는 합계라 false다.
- 현재 장바구니가 있고 요청이 그 장바구니를 고치는 내용이면 수정 action을 고른다.
- '추천해줘·골라줘·담아줘·찾아줘'처럼 상품을 달라는 요청은 상품 종류를 말하지 않았어도(예: '아들 생일인데 먹을 음식 추천해줘')
  question이 아니라 create_cart다. question은 장바구니와 무관한 질문·인사일 때만 고른다.
- '음식·먹을 거·먹거리'처럼 종류를 정하지 않는 말은 category_or_purpose·keywords에 넣지 않는다.
- 가족 프로필 이름이 상품 이름과 같을 수 있다 (예: 프로필 '라면'). 사람으로 쓴 말('라면이 먹을 피자'의 '라면')은
  target_profiles에만 넣고 category_or_purpose·keywords·groups에는 넣지 않는다.
- 여러 종류를 따로 원하면(예: '점심이랑 과자', '반찬 2개하고 라면 3개') groups에 종류별로 나누고 개수도 나눠 적는다.
- situation은 이번 문장에 상황(소풍·캠핑·야식·손님상·다이어트 등)이 직접 나왔을 때만 채운다.
  아침·점심·저녁 같은 식사 시간은 situation이 아니라 category_or_purpose다. 이전 대화나 추측으로 채우지 않는다.
- 이전 대화는 '그거', '아까 그 상품', '아까처럼'처럼 이번 문장만으로 뜻이 통하지 않을 때만 참고한다.
  situation, preferences, budget, item_count, excluded_allergens, target_profiles는 이번 문장에 나온 것만 넣는다.
  단, 챗봇이 바로 앞에서 되물었고 이번 문장이 그 답이면 이전 요청을 이어서 완성한다.

가족 프로필: {members}
현재 장바구니: {cart}
이전 대화 (오래된 것부터, 참고용):
{history}
"""

last_error: str | None = None  # 마지막 LLM 호출 실패 사유 (점검 스크립트·health에서 보여 준다)
last_model: str | None = None  # 마지막으로 실제 응답한 모델

# 키만 넣으면 쓰는 모델 목록. 앞의 모델이 실패(혼잡 503·한도 429 등)하면 다음 모델로 넘어가고,
# 모두 실패하면 규칙 파서로 대체한다. 무료로 쓸 수 있는 Gemini·Groq를 먼저 둔다.
# Gemini 별칭(*-latest)은 최신 버전을 가리켜 버전명이 바뀌어도 그대로 쓸 수 있다.
# Flash-Lite가 무료 한도가 넉넉하고 덜 붐벼서 먼저 쓴다 (10/06 점검에서 Flash는 503이 잦았다).
# Groq: llama-3.3-70b-versatile은 서비스 종료(404). 10/06 점검에서 qwen3.8-27b만 구조화 출력 7문장을 모두 통과했다
# (gpt-oss-120b·20b는 구조화 출력 형식 오류 400이 났다).
DEFAULT_MODELS = [
    ("GOOGLE_API_KEY", "google_genai:gemini-flash-lite-latest"),
    ("GOOGLE_API_KEY", "google_genai:gemini-flash-latest"),
    ("GROQ_API_KEY", "groq:qwen/qwen3.8-27b"),
    ("ANTHROPIC_API_KEY", "anthropic:claude-sonnet-5-5"),
    ("OPENAI_API_KEY", "openai:gpt-4o-mini"),
]

_llms: dict[str, object] = {}


def llm_models() -> list[str]:
    """시도할 모델 순서. LLM_MODEL을 지정하면 그 모델을 맨 앞에 둔다."""
    models = [model for key, model in DEFAULT_MODELS if os.getenv(key)]
    chosen = os.getenv("LLM_MODEL")
    if chosen:
        models = [chosen] + [m for m in models if m != chosen]
    return models


def llm_model_name() -> str | None:
    models = llm_models()
    return models[0] if models else None


def _get(model: str):
    if model not in _llms:
        from langchain.chat_models import init_chat_model

        # 무료 등급은 혼잡·한도 오류가 잦다. 한 모델에서 오래 기다리지 않고 다음 모델로 넘어가도록 재시도를 줄인다.
        _llms[model] = init_chat_model(model, temperature=0, timeout=20, max_retries=1)
    return _llms[model]


def get_llm():
    """첫 번째 모델 (모델이 없으면 None). 일반 질문 응답처럼 간단한 호출에 쓴다."""
    model = llm_model_name()
    return _get(model) if model else None


def invoke_with_fallback(build, payload):
    """build(chat_model) 로 만든 체인을 모델 순서대로 시도한다. (결과, 응답한 모델)을 돌려준다."""
    global last_error, last_model
    errors = []
    for model in llm_models():
        try:
            result = build(_get(model)).invoke(payload)
            last_error, last_model = None, model
            return result, model
        except Exception as error:  # 혼잡·한도·네트워크 오류면 다음 모델로
            errors.append(f"{model.split(':')[-1]}: {type(error).__name__}: {str(error)[:150]}")
            print(f"[llm] {model} 실패, 다음으로 넘어감: {errors[-1]}")
    last_error = " / ".join(errors)[:600] if errors else None
    raise RuntimeError(last_error or "사용할 LLM이 없습니다")


def _cart_text(cart: list[dict]) -> str:
    return ", ".join(f"{i}. {item['name']}" for i, item in enumerate(cart, 1)) or "없음"


HISTORY_TURNS = 3  # LLM에 넘길 이전 대화 수 (사용자 문장 + 챗봇 답 요약)


def _history_text(history: list[dict]) -> str:
    lines = []
    for turn in history[-HISTORY_TURNS:]:
        lines.append(f"사용자: {turn['user']}")
        lines.append(f"챗봇{'(되물음)' if turn.get('asked') else ''}: {turn['bot']}")
    return "\n".join(lines) or "없음"


def _ground_text(message: str, history: list[dict]) -> str:
    """값의 근거로 인정할 문장. 이번 문장 + (챗봇이 바로 앞에서 되물었다면) 되묻기 전의 요청 문장."""
    if history and history[-1].get("asked"):
        return f"{history[-1]['user']} {message}"
    return message


def _ground(intent: dict, message: str, history: list[dict]) -> dict:
    """LLM이 이전 대화에서 끌어온 값을 걸러낸다. 이번 요청 문장에 근거가 있는 값만 남긴다.

    프롬프트로 지시해도 LLM이 지키지 않을 수 있으므로, 상황·예산·개수·사람은 코드가 원문과 대조한다.
    (알레르기 성분은 더 빼는 쪽이라 안전하고, 답변 첫 줄에 항상 표시되므로 거르지 않는다.)
    """
    from .profiles import nospace
    from .situations import find_preset

    source = _ground_text(message, history)
    flat = nospace(source)
    dropped = []
    said = intent.get("situation")
    if said and not (find_preset(source) or nospace(said) in flat):
        intent["situation"], intent["situation_prefer"], intent["situation_avoid"] = None, [], []
        dropped.append(f"상황={said}")
    if intent.get("budget") is not None and not re.search(r"\d|원", source):
        dropped.append(f"예산={intent['budget']}")
        intent["budget"] = None
        intent["budget_min"], intent["budget_mode"], intent["per_item"] = None, None, False
    if intent.get("item_count") is not None and not re.search(r"\d|개|가지|종류|하나|둘|셋|넷", source):
        dropped.append(f"개수={intent['item_count']}")
        intent["item_count"] = None
    people = intent.get("target_profiles", [])
    intent["target_profiles"] = [w for w in people if nospace(w) in flat]
    dropped += [f"사람={w}" for w in people if w not in intent["target_profiles"]]
    if dropped:
        print(f"[intent] 이번 문장에 근거가 없어 뺀 값: {', '.join(dropped)}")
    return intent


def parse(message: str, member_names: list[str], cart: list[dict],
          mention_words: list[str] | None = None, history: list[dict] | None = None) -> tuple[dict, str]:
    """(intent, 사용한 파서 이름)을 돌려준다. history는 이전 대화 [{user, bot, asked}] (LLM에만 넘긴다)."""
    history = history or []
    if llm_models():
        from langchain_core.prompts import ChatPromptTemplate

        prompt = ChatPromptTemplate.from_messages([("system", SYSTEM_PROMPT), ("human", "{message}")])
        try:
            intent, _ = invoke_with_fallback(
                lambda llm: prompt | llm.with_structured_output(ShoppingIntent),
                {"members": ", ".join(member_names) or "없음", "cart": _cart_text(cart),
                 "history": _history_text(history), "message": message},
            )
            return _clean(_drop_person_words(_ground(intent.model_dump(), message, history), message)), "llm"
        except Exception:  # 모든 모델이 실패해도 시연이 이어지도록 규칙 파서로 넘긴다
            print("[intent] 모든 LLM 실패, 규칙 파서로 대체")
            return _clean(rule_parse(message, member_names, cart, mention_words)), "rule-fallback"
    return _clean(rule_parse(message, member_names, cart, mention_words)), "rule"


def _drop_person_words(intent: dict, message: str) -> dict:
    """사람을 가리킨 말을 상품 종류에서 뺀다 ('라면'이라는 이름의 프로필: "라면이 먹을 피자" → 대상 라면, 종류 피자).

    LLM이 '라면'을 대상(target_profiles)과 상품 종류(category·groups) 양쪽에 넣는 일이 있었다 (10/07 실제 화면).
    문장에 그 말이 한 번만 나왔고 이미 사람으로 잡혔다면 상품 종류가 아니다. ("라면이 먹을 라면"처럼 두 번 나오면 둔다)
    """
    from .profiles import nospace, variants

    people = {v for w in intent.get("target_profiles", []) for v in variants(w)}
    flat = nospace(message)
    person = lambda word: bool(word) and nospace(word) in people and flat.count(nospace(word)) <= 1
    if not any(person(w) for w in [intent.get("category_or_purpose"), *intent.get("keywords", []),
                                   *[g.get("label") for g in intent.get("groups") or []]]):
        return intent
    words = [w for w in [intent.get("category_or_purpose"), *intent.get("keywords", [])] if w and not person(w)]
    intent["category_or_purpose"], intent["keywords"] = (words[0] if words else None), words[1:]
    groups = [g for g in intent.get("groups") or [] if not person(g.get("label"))]
    for g in groups:
        g["keywords"] = [w for w in g.get("keywords", []) if not person(w)]
    intent["groups"] = groups if len(groups) >= 2 else []
    print(f"[intent] 사람 이름이라 상품 종류에서 뺌: {sorted(people)}")
    return intent


def _clean(intent: dict) -> dict:
    """LLM이 준 성분명을 19종 표준명으로 맞추고, 사전에 없는 값은 버린다."""
    names = []
    for term in intent.get("excluded_allergens", []):
        name = normalize_allergen(term)
        if name and name not in names:
            names.append(name)
    intent["excluded_allergens"] = names
    return intent


# ---------- 규칙 기반 대체 파서 ----------

_VOCAB = sorted(
    set(PURPOSE_MAP) | {"과자", "쿠키", "젤리", "견과", "음료", "주스", "두유", "유제품", "요거트", "치즈",
                        "우유", "베이커리", "식빵", "머핀", "간편식", "볶음밥", "죽", "파스타",
                        "반찬", "나물", "과일", "사과", "바나나", "정육", "시리얼",
                        # 컬리 수집 데이터 카테고리 (밀키트 / 치킨·피자·핫도그·만두 / 라면)
                        "밀키트", "라면", "컵라면", "만두", "교자", "딤섬", "피자", "치킨", "핫도그", "너겟",
                        "떡볶이", "찌개", "전골", "국밥", "짬뽕", "짜장", "우동", "쌀국수", "칼국수", "국수",
                        "냉면", "리조또", "돈까스", "카레", "커리", "덮밥", "볶음",
                        "콜라", "사이다", "탄산음료", "탄산수", "생수", "커피"},
    key=len, reverse=True,
)
_EXCLUDE = re.compile(r"([가-힣·,/\s]+?)\s*(없이|빼고|제외|없는|안\s*들어|알레르기|프리)")
_KOREAN_NUM = {"한": 1, "두": 2, "세": 3, "네": 4, "다섯": 5, "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9, "열": 10}


_AMOUNT = re.compile(r"(\d+)\s*만\s*(?:(\d+)\s*천)?\s*원?|(\d+)\s*천\s*원|(\d[\d,]{3,})\s*원|(?<![\d가-힣])(만)\s*원")
# 금액 바로 뒤의 말로 뜻을 정한다. '선에서'(이내)가 '선'(안팎)보다 먼저 걸리도록 순서를 지킨다.
_MODE_WORDS = [("min", r"^\s*(?:에\s*)?(이상|넘게|넘는|넘어|초과|부터|보다\s*비싼)"),
               ("max", r"^\s*(?:에\s*)?(선에서|이내|이하|안에서|안으로|까지|미만|아래|으로|로)"),
               ("around", r"^\s*(?:에\s*)?(정도|쯤|가깝게|가까운|안팎|내외|전후|언저리|선)")]


def _amount(m: re.Match) -> int:
    if m.group(1):
        return int(m.group(1)) * 10000 + int(m.group(2) or 0) * 1000
    if m.group(3):
        return int(m.group(3)) * 1000
    if m.group(4):
        return int(m.group(4).replace(",", ""))
    return 10000  # '만원'만 쓴 경우


def _budget(text: str) -> int | None:
    m = _AMOUNT.search(text)
    return _amount(m) if m else None


def _budget_detail(text: str) -> dict:
    """규칙 파서용: 금액 뒤의 말로 예산의 뜻을 정한다 (이상·정도·이내, 1~2만원, 만원짜리)."""
    found = list(_AMOUNT.finditer(text))
    if not found:
        return {}
    last = found[-1]
    after = text[last.end():]
    mode = next((name for name, pat in _MODE_WORDS if re.match(pat, after)), None)
    detail = {"budget": _amount(last), "budget_mode": mode, "budget_min": None,
              "per_item": bool(re.match(r"^\s*짜리", after) or re.search(r"개당|하나에|한\s*개에", text))}
    # '1~2만원', '1만~2만원', '만원에서 2만원'
    rng = re.search(r"(\d+)\s*(?:만\s*원?)?\s*[~\-]\s*(\d+)\s*만", text)
    if rng:
        detail["budget_min"] = int(rng.group(1)) * 10000
        detail["budget"] = int(rng.group(2)) * 10000
    elif len(found) >= 2 and re.search(r"에서|부터", text[found[0].end():found[1].start()]):
        detail["budget_min"] = _amount(found[0])
    return detail


def _count(text: str) -> int | None:
    if m := re.search(r"(\d+)\s*(?:개|가지|종류|종)", text):
        return int(m.group(1))
    if m := re.search(r"(다섯|여섯|일곱|여덟|아홉|한|두|세|네|열)\s*(?:개|가지)", text):
        return _KOREAN_NUM[m.group(1)]
    return None


def _terms(text: str) -> list[str]:
    """문장에 나온 상품·목적 낱말을 문장 순서대로."""
    found, rest = [], text
    for word in _VOCAB:
        if word in rest:
            found.append(word)
            rest = rest.replace(word, " " * len(word))
    return sorted(found, key=text.find)


def _find_mentions(text: str, words: list[str]) -> tuple[list[str], str]:
    """낱말 단위로 사람 표현을 찾는다 ('아이스크림'의 '아이'는 잡지 않고, '아들이랑'은 '아들'로 잡는다).

    반환: (찾은 표현들, 그 낱말만 지운 원문) — 금액의 쉼표·물음표 같은 나머지 글자는 그대로 둔다.
    """
    from .profiles import nospace, variants

    vocab = {nospace(w) for w in words if w}
    tokens = list(re.finditer(r"[가-힣A-Za-z0-9]+", text))
    found, spans, i = [], [], 0
    while i < len(tokens):
        word = tokens[i].group()
        second = tokens[i + 1].group() if i + 1 < len(tokens) else ""
        pair = next((v for v in variants(second) if nospace(word + v) in vocab), None) if second else None
        if pair:  # '첫째 아들' 같은 두 낱말
            found.append(f"{word} {pair}")
            spans.append((tokens[i].start(), tokens[i + 1].end()))
            i += 2
            continue
        single = next((v for v in variants(word) if v in vocab), None)
        if single:
            found.append(single)
            spans.append((tokens[i].start(), tokens[i].end()))
        i += 1
    for a, b in reversed(spans):
        text = text[:a] + " " + text[b:]
    return list(dict.fromkeys(found)), text


def rule_parse(message: str, member_names: list[str], cart: list[dict],
               mention_words: list[str] | None = None) -> dict:
    intent = ShoppingIntent(action="question").model_dump()
    intent["target_profiles"], text = _find_mentions(message, mention_words or member_names)

    index = re.search(r"(\d+)\s*번", text)
    intent["item_index"] = int(index.group(1)) if index else None

    has_cart = bool(cart)
    if has_cart and "대신" in text:
        before, after = text.split("대신", 1)
        intent["action"] = "replace_item"
        intent["from_keyword"] = (_terms(before) or [None])[-1]
        intent["to_keyword"] = (_terms(after) or [None])[0]
        return intent
    if has_cart and re.search(r"바꿔|교체|다른\s*(걸|것|상품)", text):
        intent["action"] = "replace_item"
        intent["to_keyword"] = (_terms(re.sub(r"\d+\s*번", " ", text)) or [None])[0]
        return intent
    if has_cart and re.search(r"빼\s*줘|빼줄|삭제|제거|지워", text):
        intent["action"] = "remove_item"
        intent["from_keyword"] = (_terms(text) or [None])[0]
        return intent
    if has_cart and re.search(r"저렴|싸게|싼|가격.*낮", text):
        intent["action"] = "cheaper"
        return intent

    # 여기부터는 생성·추가·조건 변경
    def take_allergens(match: re.Match) -> str:
        names, rest = split_allergens(match.group(1))
        intent["excluded_allergens"].extend(n for n in names if n not in intent["excluded_allergens"])
        return f" {rest} " if names else match.group(0)

    text = _EXCLUDE.sub(take_allergens, text)

    intent.update(_budget_detail(text))
    intent["item_count"] = _count(text)
    terms = _terms(text)
    if terms:
        intent["category_or_purpose"], intent["keywords"] = terms[0], terms[1:]
    # '점심이랑 과자'처럼 두 종류 이상을 이어 말하면 묶음으로 나눈다
    rest = text
    for t in terms:
        rest = rest.replace(t, " ")  # 낱말을 지우고 남은 연결어('이랑', '하고' 등)를 본다 ('과자'의 '과'는 안 잡힘)
    if len(terms) >= 2 and re.search(r"(이랑|랑|하고|와|과|및|그리고|,)\s", rest + " "):
        each = re.search(r"(\d+)\s*개씩", text)
        intent["groups"] = [{"label": t, "keywords": [t], "count": int(each.group(1)) if each else None} for t in terms]
    from .situations import find_preset
    intent["situation"] = find_preset(message)

    asks_how = re.search(r"어떻게|어떤\s*게|뭐가|무엇|왜|궁금|차이|알려\s*줘|\?$", text.strip())
    if asks_how and not (intent["budget"] or intent["item_count"] or intent["excluded_allergens"]):
        intent["action"] = "question"  # "간식은 어떻게 고르면 좋아?" 같은 일반 질문
    elif has_cart and re.search(r"추가|더\s*담|더\s*넣|하나\s*더", text):
        intent["action"] = "add_item"
        intent["to_keyword"] = terms[0] if terms else None
    elif has_cart and not terms and (intent["budget"] or intent["item_count"]):
        intent["action"] = "update_constraints"
    elif terms or intent["budget"] or intent["item_count"] or intent["excluded_allergens"] \
            or re.search(r"장바구니|담아|추천|골라|만들|구성|짜\s*줘|찾아", text):
        intent["action"] = "create_cart"
    return intent
