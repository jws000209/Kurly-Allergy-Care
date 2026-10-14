"""상황 맞춤: '소풍', '캠핑' 같은 상황을 상품명 기준의 우선·제외 낱말로 바꾼다.

- 상황은 그 문장에서 말했을 때만 쓰고, 그 추천과 그 추천을 고치는 요청에만 적용한다.
  새 추천 요청이 오면 초기화된다 (대화 상태에 오래 남아 다음 장보기에 끼어들지 않게).
- 프리셋과 LLM 제안을 병합하고 명시 상품 요청을 우선한다. 부정된 상황은 적용하지 않는다.
- 우선·감점은 취향 순위이며 후보를 삭제하거나 알레르기 판정을 바꾸지 않는다.
"""

import re

# 상황 이름 → (같은 뜻의 말, 우선 낱말, 감점 낱말)
PRESETS = {
    "소풍": {
        "aliases": ["소풍", "피크닉", "나들이", "도시락", "들놀이"],
        "prefer": ["김밥", "주먹밥", "샌드위치", "유부초밥", "핫도그", "너겟", "치킨", "닭강정", "또띠아", "브리또", "과자", "간식"],
        "avoid": ["찌개", "전골", "탕", "국밥", "칼국수", "우동", "짬뽕", "짜장", "국수", "죽", "밀키트", "라면", "면"],
        "why": "들고 가서 바로 먹기 좋은 상품 우선, 끓이거나 조리해야 하는 상품 제외",
    },
    "캠핑": {
        "aliases": ["캠핑", "글램핑", "바베큐", "바비큐"],
        "prefer": ["바베큐", "구이", "소시지", "스테이크", "꼬치", "라면", "찌개", "전골", "떡볶이", "삼겹"],
        "avoid": [],
        "why": "불에 굽거나 끓여 먹기 좋은 상품 우선",
    },
    "야식": {
        "aliases": ["야식", "밤참", "술안주", "안주"],
        "prefer": ["치킨", "피자", "떡볶이", "라면", "족발", "만두", "핫도그", "닭강정"],
        "avoid": [],
        "why": "늦은 시간 간단히 데워 먹는 메뉴 우선",
    },
    "손님상": {
        "aliases": ["손님", "집들이", "파티", "명절"],
        "prefer": ["전골", "스테이크", "갈비", "피자", "파스타", "세트", "2인", "3인", "4인"],
        "avoid": ["컵", "큰컵", "1인"],
        "why": "여럿이 나눠 먹는 메뉴 우선, 1인용·컵 제품 제외",
    },
    "생일": {
        "aliases": ["생일", "생신", "돌잔치", "백일"],
        "prefer": ["케이크", "미역국", "잡채", "갈비", "불고기", "떡", "과일", "피자", "치킨"],
        "avoid": [],
        "why": "생일상에 어울리는 미역국·잡채·갈비·케이크 우선",
    },
    "다이어트": {
        "aliases": ["다이어트", "식단", "저칼로리", "건강식"],
        "prefer": ["닭가슴살", "샐러드", "곤약", "저당", "저칼로리", "현미", "두부"],
        "avoid": ["튀김", "치즈", "크림", "버터"],
        "why": "가볍고 단백질 위주 상품 우선, 튀김·치즈·크림 제외",
    },
}


# 아침·점심·저녁은 상황이 아니라 상품 목적(검색어)으로 다룬다
MEAL_TIMES = {"아침", "점심", "저녁", "식사", "한끼", "끼니", "브런치"}


AMBIGUOUS_ALIASES = {"도시락", "식단", "건강식"}
NEGATION = re.compile(r"아니|아닌|말고|취소|제외|안\s*(?:해|하|할|가|갈|감)|가지\s*않|하지\s*않")


def positive_presets(text):
    text = text or ""
    matches = [(m.start(), m.end(), name, alias) for name, preset in PRESETS.items()
               for alias in preset["aliases"] if alias not in AMBIGUOUS_ALIASES
               for m in re.finditer(re.escape(alias), text)]
    out = []
    for start, end, name, alias in matches:
        next_start = min((a for a, _, _, _ in matches if a >= end), default=len(text))
        tail = re.split(r"[.!?\n,]", text[end:next_start], maxsplit=1)[0][:25]
        if not NEGATION.search(tail):
            out.append((name, start, alias))
    return out


def find_preset(text: str | None) -> str | None:
    matches = positive_presets(text)
    if not matches:
        return None
    # Specific event names outrank generic party/guest words; then use sentence order.
    return min(matches, key=lambda m: (m[2] in {"손님", "파티", "안주"}, m[1]))[0]


def grounded(situation, source):
    preset = find_preset(situation)
    if preset:
        return preset in {name for name, _, _ in positive_presets(source)}
    if not situation or situation.strip() in MEAL_TIMES:
        return False
    match = re.search(re.escape(situation.strip()), source or "")
    return bool(match) and not NEGATION.search((source or "")[match.end():].split(".")[0][:25])


def _clean_words(words: list[str] | None, limit: int = 10) -> list[str]:
    out = []
    for word in words or []:
        word = str(word).strip()
        if 2 <= len(word) <= 10 and word not in out:
            out.append(word)
    return out[:limit]


def build(situation: str | None, llm_prefer: list[str] | None = None, llm_avoid: list[str] | None = None,
          explicit_terms: list[str] | None = None) -> dict | None:
    """상황 조건을 만든다. 알려진 상황이면 표를, 아니면 LLM 제안 낱말을 쓴다."""
    if not situation:
        return None
    preset = find_preset(situation)
    if preset:
        p = PRESETS[preset]
        prefer = _clean_words(p["prefer"] + (llm_prefer or []), 20)
        avoid = _clean_words(p["avoid"] + (llm_avoid or []), 20)
        avoid = [w for w in avoid if w not in prefer and not any(w in t or t in w for t in explicit_terms or [] if t)]
        return {"name": preset, "said": situation, "prefer": prefer, "avoid": avoid,
                "why": p["why"].replace("제외", "낮은 우선순위"), "source": "혼합" if llm_prefer or llm_avoid else "규칙", "filter_mode": "soft"}
    prefer, avoid = _clean_words(llm_prefer), _clean_words(llm_avoid)
    avoid = [w for w in avoid if w not in prefer and not any(w in t or t in w for t in explicit_terms or [] if t)]
    if situation.strip() in MEAL_TIMES or (not prefer and not avoid):
        return None  # 식사 시간이거나 기준이 없는 상황은 상황 조건으로 쓰지 않는다
    return {"name": situation, "said": situation, "prefer": prefer, "avoid": avoid,
            "why": "LLM이 제안한 낱말 기준", "source": "LLM", "filter_mode": "soft"}


def describe(situation: dict | None) -> str | None:
    if not situation:
        return None
    if not situation["prefer"] and not situation["avoid"]:
        return f"상황: {situation['name']} — 이 상황에 맞춘 기준이 없어 일반 추천으로 골랐어요."
    parts = []
    if situation["prefer"]:
        parts.append("우선 " + "·".join(situation["prefer"][:5]) + ("…" if len(situation["prefer"]) > 5 else ""))
    if situation["avoid"]:
        parts.append("낮은 우선순위 " + "·".join(situation["avoid"][:5]) + ("…" if len(situation["avoid"]) > 5 else ""))
    return f"상황: {situation['name']} ({', '.join(parts)}) — 이번 추천과 그 수정에만 적용"
