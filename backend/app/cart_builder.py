"""Candidate Search/Ranker + Cart Builder + Cart Revalidation.

여기서 다루는 후보는 모두 Rule Engine을 PASS한 상품이다.
조건이 안 맞아도 알레르기 조건은 완화하지 않고, 부족하다는 사실만 돌려준다.
"""
import re

from . import db, recommender
from .rule_engine import BLOCK, PASS, UNVERIFIED, judge

DEFAULT_COUNT = 5

# 장보기 목적 → 그 목적에 맞는 소분류 (목적어는 상품명에서 찾지 않는다: '아침에 버터', '아침햇살' 같은 이름 오탐)
_MEAL = ["밀키트", "도시락/밥류", "국/탕/찌개", "메인요리", "짜장/짬뽕/파스타/면류", "떡볶이/튀김/순대", "라면"]
PURPOSE_MAP = {
    "간식": ["과자/간식", "대용량 과자/간식", "유아 과자/간식", "쿠키/비스킷/크래커", "초콜릿/젤리/캔디", "떡/한과",
             "간식빵", "디저트", "아이스크림"],
    "디저트": ["디저트", "케이크", "타르트/파이", "아이스크림", "초콜릿/젤리/캔디", "떡/한과"],
    "아침": ["식빵/모닝빵/베이글", "선식/시리얼", "우유/두유", "요거트/생크림", "샐러드/샌드위치", "잼/스프레드",
             "간편과일", "죽/스프/카레"],
    "식사": _MEAL, "점심": _MEAL, "한끼": _MEAL,
    "저녁": _MEAL + ["국내산 돼지고기", "국내산 소고기", "양념육", "밑반찬"],
    "야식": ["치킨/피자/핫도그/만두", "떡볶이/튀김/순대", "라면", "양념육", "조미오징어/어포/쥐포"],
    "마실": ["생수/얼음", "탄산수", "탄산/스포츠음료", "과일/야채음료", "차음료", "커피음료", "어린이음료/선물세트"],
    "빵": ["식빵/모닝빵/베이글", "간식빵"],
    "고기": ["국내산 돼지고기", "국내산 소고기", "수입산 돼지고기/양고기", "수입산 소고기", "닭/오리고기", "양념육"],
    "반찬": ["밑반찬", "김치/젓갈/장류", "두부/어묵/부침개", "메인요리"],
}
# 분류 이름과 낱말이 다른 경우: 낱말 → 그 낱말을 뜻하는 소분류
TERM_CATEGORY = {
    "주스": ["과일/야채음료"], "닭고기": ["닭/오리고기"], "오리고기": ["닭/오리고기"], "생선": ["생선류"],
    "조개": ["해산물/전복/조개류"], "차": ["곡물차/전통차", "허브차/꽃차/과일차", "홍차/녹차/보이차", "차음료",
                                       "코코아/밀크티/기타 차"],
    "탄산음료": ["탄산/스포츠음료"], "음료수": ["과일/야채음료", "탄산/스포츠음료", "차음료"],
}
# 과일·채소 이름: 과일·채소 분류의 상품을 맛 이름으로 쓴 가공품('딸기 우유', '사과 요거트')보다 앞에 둔다
FRESH = {"사과", "배", "딸기", "바나나", "포도", "샤인머스캣", "귤", "감귤", "한라봉", "오렌지", "레몬", "자몽", "키위",
         "망고", "복숭아", "수박", "참외", "멜론", "블루베리", "체리", "감", "아보카도", "파인애플", "토마토", "방울토마토",
         "당근", "감자", "고구마", "양파", "대파", "마늘", "배추", "양배추", "브로콜리", "파프리카", "오이", "호박",
         "애호박", "고추", "시금치", "상추", "깻잎", "버섯", "콩나물", "무"}
_FRESH_TOPS = ("과일/견과/쌀", "채소")
# 묶인 소분류 안에서 종류를 알아보는 다른 이름 (예: '왕교자'는 만두라서 '피자'·'치킨' 요청에 넣지 않는다)
PART_SYNONYMS = {
    "만두": ["교자", "딤섬", "하가우", "샤오롱바오", "바오", "완탕", "훈툰"],
    "치킨": ["닭", "너겟", "텐더", "윙", "봉", "가라아게", "강정"],
    "핫도그": ["콘도그", "소시지"],
    "피자": ["피자"],
    "짜장": ["자장"],
    "파스타": ["스파게티", "까르보나라", "알리오", "라자냐", "뇨끼", "링귀니", "펜네", "라구"],
    "면류": ["국수", "우동", "라멘", "소바", "냉면", "쫄면", "탕면", "멘"],
}

# 요청 낱말을 품었지만 다른 상품인 말 ('콜라' 요청에 '쇼콜라'·'콜라겐'·'콜라비'가 걸리지 않게, 10/07 실제 화면)
LOOKALIKE = {
    "콜라": ["쇼콜라", "콜라겐", "콜라비"],
}

# 상품명의 마지막 낱말(무엇인지 나타내는 말)을 찾을 때 건너뛰는 말
_TAIL_WORDS = {"세트", "선물세트", "키트", "대용량", "냉동", "냉장", "생물", "실속", "특", "대", "중", "소", "set", "SET"}


# "2종 (택1)", "(맵기선택)"처럼 옵션을 골라야 하는 상품. 컬리 장바구니에 자동으로 담을 수 없어 뒤로 미룬다.
_OPTION_PATTERN = re.compile(r"택\s*\d|선택\s*\)")


def _name_tokens(name: str) -> list[str]:
    """브랜드 [ ], 괄호 ( ), 숫자 든 낱말(용량·개수)을 뺀 상품명 낱말들."""
    name = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", name)
    return [w for w in re.split(r"[\s,/+&·]+", name) if w and not re.search(r"\d", w)]


def _name_match(name: str, term: str) -> int:
    """2: 상품이 그것이다 ('시카고 딥디쉬 피자', '삼각김밥', '된장국') / 1: 꾸미는 말로만 들어감 ('피자치즈', '김밥 단무지')."""
    tokens = _name_tokens(name)
    heads = [w for w in tokens if w not in _TAIL_WORDS]
    if heads and heads[-1].endswith(term):
        return 2
    if len(term) >= 2 and term in " ".join(tokens):
        return 1
    return 0


def _term_score(product: dict, term: str) -> int:
    category = product["category"]
    top, _, leaf = category.rpartition(">")
    parts = leaf.split("/")
    if term in PURPOSE_MAP:  # 목적어는 연결된 소분류(또는 분류 이름에 그 말이 있는 경우)로만
        return 2 if leaf in PURPOSE_MAP[term] or term in category else 0
    if leaf in TERM_CATEGORY.get(term, []) or term == top or term == category:
        return 3
    # 단일 소분류 ('라면', '국산과일'). 한 글자 낱말은 분류 낱말의 끝만 본다 ('국'이 '국내산 돼지고기'에 걸리지 않게)
    if len(parts) == 1 and (term in leaf if len(term) >= 2 else any(w.endswith(term) for w in leaf.split())):
        return 3
    product_name = product["name"]
    for word in LOOKALIKE.get(term, []):
        product_name = product_name.replace(word, " ")
    name = _name_match(product_name, term)
    in_part = any(term == part or (len(term) >= 2 and term in part) for part in parts) if len(parts) > 1 else False
    if name == 2 and (in_part or term in top or (term in FRESH and top in _FRESH_TOPS)):
        return 3                                     # 이름과 분류가 함께 맞음 ('치킨/피자/…'의 '…피자', 과일 분류의 '사과')
    if in_part:
        # 묶인 소분류('치킨/피자/핫도그/만두', '짜장/짬뽕/파스타/면류'): 분류만으로는 무슨 종류인지 모른다.
        # 상품명에 그 종류('짜장면')가 있으면 3, 다른 종류('만두', '칼국수')가 있으면 0, 아무 것도 없으면 1.
        # (10/07: '짜장' 요청에 같은 분류의 칼국수·우동이 짜장과 같은 점수로 후기 수 순서에 앞서던 문제)
        if term in product_name:
            return 3
        others = [part for part in parts if term not in part]
        words = [w for o in others for w in [o, *PART_SYNONYMS.get(o, [])] if w]
        if any(w in product["name"] for w in words):
            return 0
        # 다른 이름('교자', '스파게티')은 괄호·숫자 낱말을 뺀 이름에서만 찾는다 ('2봉'의 '봉'을 치킨으로 보지 않게)
        plain = " ".join(_name_tokens(product["name"]))
        return 3 if any(w in plain for w in PART_SYNONYMS.get(term, [])) else 1
    if name:
        return name
    return 1 if len(term) >= 2 and term in top else 0


def _score(product: dict, terms: list[str]) -> int:
    """요청 낱말과 얼마나 맞는지 (관련도). 3: 분류가 그것 / 2: 상품명의 핵심 낱말이 그것 / 1: 약하게 걸림 / 0: 무관.

    10/07 실제 데이터 점검으로 정한 규칙:
    - '과일' → 국산과일 분류(3)가 '과일품은 참소스'(이름에 꾸미는 말로만, 1)보다 앞
    - '피자' → '…피자' 상품(3), 같은 묶인 분류 '치킨/피자/핫도그/만두'의 만두(0), '피자치즈'(1)
    - '짜장' → '짜장면 2인분'(3), 같은 묶인 분류 '짜장/짬뽕/파스타/면류'의 칼국수·우동(0), 종류를 알 수 없는 이름(1)
    - '김밥' → '삼각김밥'(2), '김밥 단무지'·'김밥햄'(1) / '국' → '된장국'(3), '국산콩 두부'(0, 한 글자는 낱말 끝만)
    - '주스'·'닭고기'·'차'처럼 분류 이름과 다른 말은 TERM_CATEGORY로 분류에 연결
    - '간식'·'아침' 같은 목적어는 PURPOSE_MAP의 소분류로만(2)
    """
    return sum(_term_score(product, term) for term in terms)


STRONG_MATCH = 2  # 상품명이나 단일 소분류가 맞는 경우


def _popularity(item: dict) -> tuple:
    """같은 관련도 안에서의 순서: 1순위 후기 수(많은 순), 2순위 판매량 순위(높은 순). 가격은 쓰지 않는다."""
    reviews = item.get("review_count")
    rank = item.get("sales_rank")
    return (-(reviews if reviews is not None else -1), rank if rank is not None else 10**6)


def to_item(product: dict, verdict: dict) -> dict:
    # 컬리는 최소 구매 수량(예: 2봉)만큼 장바구니에 담는다. 예산·합계는 그만큼 곱한 금액으로 계산한다.
    min_ea = max(1, product.get("min_ea") or 1)
    unit = product["price"] or 0
    return {
        "id": product["id"], "name": product["name"], "price": unit * min_ea, "unit_price": unit, "min_ea": min_ea,
        "review_count": product.get("review_count"), "sales_rank": product.get("sales_rank"),
        "category": product["category"], "url": product["url"], "image": product["image"],
        "source": product["source"], "status": verdict["status"], "reason": verdict["reason"],
        "cross_matched": verdict["cross_matched"], "last_checked_at": product["last_checked_at"],
        "needs_option": bool(_OPTION_PATTERN.search(product["name"])),
    }




def search(terms: list[str], avoid: list[str], strict: bool, exclude_ids: set[str] | None = None,
           cheap_first: bool = False, min_score: int = 1, situation: dict | None = None,
           user_id: int | None = None) -> tuple[list[dict], dict]:
    """요청에 맞는 상품을 Rule Engine으로 걸러 PASS 후보만 순위대로 돌려준다.

    순서: 관련도(요청 낱말 일치) → 상황 우선 낱말 → 후기 수 → 판매량 순위. 가격은 순서에 쓰지 않는다
    ('더 저렴하게'처럼 cheap_first일 때만 가격순). 예산은 cart_builder.build가 따로 맞춘다.
    situation(소풍 등)이 있으면 제외 낱말이 든 상품은 빼고, 우선 낱말이 든 상품은 같은 관련도 안에서 앞에 둔다.
    이것은 취향 조건일 뿐이고, 알레르기 판정(judge)은 상황과 무관하게 똑같이 한다.
    """
    exclude_ids = exclude_ids or set()
    prefer = (situation or {}).get("prefer", [])
    avoid_words = (situation or {}).get("avoid", [])
    stats = {"matched": 0, "situation_excluded": 0, PASS: 0, BLOCK: 0, UNVERIFIED: 0}
    ranked = []
    products = db.list_products()
    for product in products:
        score = _score(product, terms)
        if (terms and score < min_score) or product["id"] in exclude_ids:
            continue
        stats["matched"] += 1
        if avoid_words and any(w in product["name"] for w in avoid_words):
            stats["situation_excluded"] += 1
            continue
        preferred = bool(prefer) and any(w in product["name"] for w in prefer)
        verdict = judge(product, avoid, strict)
        stats[verdict["status"]] += 1
        if verdict["status"] == PASS:
            ranked.append((score, {**to_item(product, verdict), "score": score, "preferred": preferred}))
    # '피자'·'만두'처럼 구체적인 종류를 말했고 그와 분명히 맞는 상품(상품명·단일 소분류)이 있으면,
    # 약하게만 맞는 상품(같은 묶음 분류 '치킨/피자/핫도그/만두'의 만두 등)으로 빈자리를 채우지 않는다.
    # '간식'·'야식'·'점심'처럼 범위가 넓은 목적어는 여러 분류에서 채우는 게 맞으므로 그대로 둔다.
    concrete = bool(terms) and not any(t in PURPOSE_MAP for t in terms)
    if concrete and any(score >= STRONG_MATCH for score, _ in ranked):
        ranked = [pair for pair in ranked if pair[0] >= STRONG_MATCH]
    if cheap_first:
        ranked.sort(key=lambda pair: (pair[1]["needs_option"], pair[1]["price"], -pair[0]))
    else:
        # 바로 담을 수 있는 상품(옵션 없음·실제 컬리 상품)을 같은 조건이면 먼저 둔다
        ranked.sort(key=lambda pair: (-pair[0], not pair[1]["preferred"], pair[1]["needs_option"],
                                      pair[1]["source"] == "mock", *_popularity(pair[1])))
    items = [item for _, item in ranked]
    if user_id is not None and not cheap_first:
        items = recommender.personalize(items, products, db.list_interactions(), user_id)
    return items, stats


def missing_terms(terms: list[str]) -> list[str]:
    """상품 DB에 하나도 맞는 상품이 없는 낱말 (예: 과자 데이터를 수집하지 않았으면 '과자')."""
    products = db.list_products()
    return [t for t in terms if not any(_score(p, [t]) > 0 for p in products)]


def catalog_categories() -> list[str]:
    """지금 상품 DB에 있는 대분류 (없는 상품 유형을 안내할 때 보여 준다. 소분류는 100개가 넘어 너무 길다)."""
    return sorted({p["category"].partition(">")[0] for p in db.list_products() if ">" in p["category"]})


def total(cart: list[dict]) -> int:
    return sum(item["price"] for item in cart)


INF = float("inf")


def as_budget(budget) -> dict | None:
    """예산 조건 = {amount, mode, min, per_item}. 숫자만 오면 총액 상한으로 본다.

    mode: max(이내·안에서·까지) / min(이상·넘게) / around(정도·쯤·가깝게·안팎) / range(1~2만원: min~amount)
    per_item: '만원짜리', '개당'처럼 상품 하나의 가격 기준이면 True
    """
    if budget is None:
        return None
    if isinstance(budget, (int, float)):
        return {"amount": int(budget), "mode": "max", "min": None, "per_item": False}
    return {"amount": budget["amount"], "mode": budget.get("mode") or "max", "min": budget.get("min"),
            "per_item": bool(budget.get("per_item"))}


def budget_band(budget) -> tuple[tuple[float, float], tuple[float, float]]:
    """((상품 하나 하한, 상한), (합계 하한, 상한))."""
    b = as_budget(budget)
    if not b:
        return (0, INF), (0, INF)
    a, mode = b["amount"], b["mode"]
    band = {"max": (0, a), "min": (a, INF), "around": (a * 0.85, a * 1.15),
            "range": (b["min"] or 0, a)}.get(mode, (0, a))
    return (band, (0, INF)) if b["per_item"] else ((0, INF), band)


def describe_budget(budget) -> str:
    b = as_budget(budget)
    if not b:
        return ""
    a = f"{b['amount']:,}원"
    text = {"max": f"{a} 이내", "min": f"{a} 이상", "around": f"{a} 안팎",
            "range": f"{(b['min'] or 0):,}~{a}"}.get(b["mode"], f"{a} 이내")
    return ("개당 " if b["per_item"] else "예산 ") + text


def split_budget(budget, share: float):
    """묶음 추천일 때 총액 예산을 개수 비율로 나눈다 (개당 기준은 그대로)."""
    b = as_budget(budget)
    if not b or b["per_item"]:
        return b
    return {**b, "amount": int(b["amount"] * share), "min": int(b["min"] * share) if b["min"] else None}


def fits(cart: list[dict], candidate: dict, budget) -> bool:
    """장바구니를 고칠 때(추가·교체) 이 상품을 더해도 예산 조건(개당 범위·합계 상한)을 지키는지."""
    (each_lo, each_hi), (_, total_hi) = budget_band(budget)
    return each_lo <= candidate["price"] <= each_hi and total(cart) + candidate["price"] <= total_hi


def _ordered(candidates: list[dict]) -> list[dict]:
    """추천 순서: 요청에 더 잘 맞는 점수대부터, 같은 점수대 안에서는 소분류를 돌아가며 (원래 후보 순서 = 인기순 유지)."""
    order: list[dict] = []
    for score in sorted({item.get("score", 0) for item in candidates}, reverse=True):
        tier = [item for item in candidates if item.get("score", 0) == score]
        while tier:
            seen: set[str] = set()
            for item in list(tier):
                if item["category"] not in seen:
                    seen.add(item["category"])
                    order.append(item)
                    tier.remove(item)
    return order


def _pick_within(order: list[dict], n: int, lo: float, hi: float) -> list[dict] | None:
    """순서대로 훑으며 n개를 고르되, 합계가 [lo, hi] 안에 들어갈 수 있을 때만 담는다. 못 채우면 None.

    남은 자리를 뒤쪽 후보로 채울 수 있는지(가장 싼 k개 합 ≤ 남은 상한, 가장 비싼 k개 합 ≥ 남은 하한)를
    미리 계산해 두고 확인하므로, 인기 순서를 최대한 지키면서 예산을 맞춘다.
    """
    prices = [item["price"] for item in order]
    m = len(order)
    if m < n:
        return None
    # suffix_low[i][k] / suffix_high[i][k]: i번째 뒤 후보들 중 가장 싼/비싼 k개의 합 (k ≤ n)
    suffix_low = [[0.0] * (n + 1) for _ in range(m + 1)]
    suffix_high = [[0.0] * (n + 1) for _ in range(m + 1)]
    lows: list[float] = []
    highs: list[float] = []
    for i in range(m - 1, -1, -1):
        for k in range(1, n + 1):
            suffix_low[i + 1][k] = sum(lows[:k]) if len(lows) >= k else INF
            suffix_high[i + 1][k] = sum(highs[:k]) if len(highs) >= k else -INF
        lows = sorted(lows + [prices[i]])[:n]
        highs = sorted(highs + [prices[i]], reverse=True)[:n]
    picked, spent = [], 0
    for i, item in enumerate(order):
        if len(picked) == n:
            break
        k = n - len(picked) - 1
        after = spent + item["price"]
        low_rest = suffix_low[i + 1][k] if k else 0
        high_rest = suffix_high[i + 1][k] if k else 0
        if after + low_rest <= hi and after + high_rest >= lo:
            picked.append(item)
            spent = after
    return picked if len(picked) == n else None


def build(candidates: list[dict], count: int | None, budget=None) -> tuple[list[dict], list[str]]:
    """개수·예산 조건을 지키는 조합을 만든다. 순서(관련도 → 인기)를 최대한 지키고, 못 채우면 가능한 만큼 담고 이유를 알린다."""
    count = count or DEFAULT_COUNT
    notes: list[str] = []
    order = _ordered(candidates)
    if len(order) < count:
        notes.append(f"요청한 종류 중 알레르기 조건을 통과한 상품이 {len(order)}개뿐이라 {count}개를 채우지 못했어요.")
    b = as_budget(budget)
    if not b:
        return order[:count], notes

    (each_lo, each_hi), (lo, hi) = budget_band(b)
    in_range = [item for item in order if each_lo <= item["price"] <= each_hi]
    want = min(count, len(order))
    picked: list[dict] = []
    for n in range(min(want, len(in_range)), 0, -1):
        picked = _pick_within(in_range, n, lo, hi) or []
        if picked:
            break
    if len(picked) < want:
        notes.append(_budget_note(b, len(picked), in_range or order))
    return picked, notes


def _budget_note(b: dict, got: int, pool: list[dict]) -> str:
    """예산 때문에 개수를 못 채웠을 때의 안내. 0개면 왜 없는지(가장 싼/비싼 가격)를 함께 알린다."""
    cond = describe_budget(b)
    prices = [item["price"] for item in pool]
    if got:
        return f"{cond} 조건으로는 {got}개까지만 담을 수 있었어요."
    if not prices:
        return f"{cond} 조건에 맞는 상품이 없어요."
    if b["mode"] == "min":
        return f"{cond} 조건에 맞는 상품이 없어요. (조건을 통과한 상품 중 가장 비싼 상품: {max(prices):,}원)"
    return f"{cond} 조건에 맞는 상품이 없어요. (조건을 통과한 상품 중 가장 싼 상품: {min(prices):,}원)"


def revalidate(cart: list[dict], avoid: list[str], strict: bool) -> tuple[list[dict], list[str]]:
    """장바구니의 모든 상품을 Rule Engine으로 다시 검증하고, PASS가 아니면 뺀다."""
    kept, notes = [], []
    for item in cart:
        product = db.get_product(item["id"])
        if product is None:
            notes.append(f"'{item['name']}'은(는) 상품 DB에서 찾을 수 없어 제외했어요.")
            continue
        verdict = judge(product, avoid, strict)
        if verdict["status"] == PASS:
            kept.append({**item, **to_item(product, verdict)})  # 묶음 이름 등 붙어 있던 정보는 유지
        else:
            notes.append(f"재검증에서 '{item['name']}' 제외: {verdict['reason']}")
    return kept, notes
