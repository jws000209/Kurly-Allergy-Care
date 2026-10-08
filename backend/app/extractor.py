"""Ingredient Allergen Extractor: 원재료명·알레르기 표시 문구 → 19종 알레르겐."""
import re

from .allergens import ALLERGENS

# '혼입 가능', '같은 제조시설' 문구는 함유가 아니라 교차오염 가능성으로 따로 관리한다.
_CROSS_PATTERN = re.compile(
    r"혼입|교차|제조\s*(시설|라인|공간)|(같은|동일|동일한)\s*(시설|공간|라인|공장)|사용(한|하는)\s*제품과"
)
# 컬리 표시문은 줄바꿈 없이 한 줄로 오기도 한다 ("- 우유, 밀 함유 - 이 제품은 … 같은 제조시설에서 …").
# 그래서 줄·문장 경계뿐 아니라 '함유' 뒤, '- ' 앞, '이 제품은/본 제품은' 앞에서도 구간을 나눈다.
_SEGMENT_SPLIT = re.compile(
    r"\n|\.\s|\.$|※|\*|(?<=함유)|(?:^|\s)-\s*|(?=(?:이|본|해당)\s*제품은)"
    r"|(?<=있음)|(?<=혼입가능)|(?<=혼입\s가능)(?!성)|(?<=가능성)"  # "…혼입 가능성 있음 대두, 밀 함유"
)
_TOKEN_SPLIT = re.compile(r"[\s,()\[\]{}/·:;%\-]+")
# 표시문 뒤에 붙어 온 페이지 문구(소비기한·상품선택 등)는 판정에서 뺀다.
_TRAILING = re.compile(r"소비기한\s*\(|상품선택|안내사항")
# 알레르기 표시가 비어 있지만 팀 검토로 원물·단순 구성(채소·과일·쌀·생수·블랙커피·잎차·오일·소금 등)이라
# 19종이 없다고 판단한 상품. 가져오기 때 빈 칸 대신 이 표시를 넣는다. 라벨로 확인한 '없음'과 구분해 화면에 밝힌다.
NO_LABEL_REVIEWED = "[알레르기 표시 없음 · 원물·단순 구성으로 19종 없음 판단]"

# 컬리 표시문에서 실제로 발견한 오탈자 (10/07 v2 데이터 분석). 못 읽으면 함유 성분을 놓쳐 PASS가 되므로 매칭 전에 고친다.
# 다른 낱말의 일부가 바뀌지 않도록 앞뒤가 한글이 아닐 때만 바꾸는 것(_B)은 경계를 둔다.
_B = r"(?<![가-힣]){}(?![가-힣])"
_TYPOS = [(re.compile(p), fix) for p, fix in [
    (_B.format("대우"), "대두"), (_B.format("유유"), "우유"), ("우윰", "우유"),
    ("함윰|함융", "함유"),
    ("달고기|닥고기|닭고가", "닭고기"), ("쇄고기|쐬고기", "쇠고기"),
    ("이산화항", "이산화황"), ("아항산|아황선|아왕산", "아황산"), (_B.format("아왕"), "아황산"),
    ("홍함", "홍합"), (_B.format("메일"), "메밀"),
    ("오장어|오징오|오징아|오정어", "오징어"), ("고드어|고둥어|고등오", "고등어"),
    (_B.format("세우"), "새우"), ("토미토|토마도|토마트", "토마토"), ("복숭이|복승아", "복숭아"),
    (r"(?<=[,\s])개(?=\s*,)", "게"),   # 성분 목록 안의 '개' (예: '고등어, 개, 세우')
]]


def fix_typos(text: str) -> str:
    for pattern, fix in _TYPOS:
        text = pattern.sub(fix, text)
    return text


# '게를 사용한', '게와 같은'처럼 한 글자 성분명 뒤에 조사가 붙은 경우도 낱말 일치로 본다.
_TOKEN_PARTICLE = re.compile(r"(를|을|와|과|및|등|이|가|은|는|도)$")

# 함유 표시 문장: '…함유' 또는 끝이 '…포함'인 구간 ('조개류(굴, 홍합 포함)'처럼 괄호 안 '포함'은 아님)
_STATED = re.compile(r"[가-힣)]\s*함유|[가-힣]\s*포함[\s.,]*$")  # '연체류(낙지) 함유'처럼 괄호 뒤 함유 포함
# 알레르기 표시가 아닌 '함유' 주의 문구 (함유 표시 문장으로 보지 않는다)
_NOT_ALLERGY_NOTICE = re.compile(r"당알코올|페닐알라닌|카페인|알코올|나트륨|과량\s*섭취")

# 알레르겐이 하나도 없다는 것을 분명히 밝힌 표시
_NONE_PATTERN = re.compile(r"해당\s*(사항)?\s*없|알레르기\s*유발\s*(물질|성분|식품)?\s*(은|이)?\s*없|^\s*-?\s*없음")


def _match(segment: str) -> set[str]:
    found = set()
    tokens = set(_TOKEN_SPLIT.split(segment))
    tokens |= {_TOKEN_PARTICLE.sub("", tok) for tok in tokens if len(tok) >= 2}
    for allergen in ALLERGENS:
        text = segment
        for mask in allergen.get("masks", []):
            text = text.replace(mask, " ")
        if any(s in text for s in allergen["synonyms"]) or any(
            e in tokens for e in allergen.get("exact", [])
        ):
            found.add(allergen["name"])
    return found


def extract(ingredients: str | None, allergy_label: str | None) -> dict:
    """반환: contains(함유), cross(혼입 가능), verified(판정할 근거가 있는지).

    원재료명이 있으면 그것으로 판정할 수 있다. 알레르기 표시문만 있을 때는
    알레르겐이 실제로 적혀 있거나 '해당 없음'이 명시된 경우에만 verified로 본다.
    ('옵션별 상이하여 상세페이지 참고' 같은 문구는 판정 근거가 아니다.)
    """
    if (allergy_label or "").strip() == NO_LABEL_REVIEWED and not (ingredients or "").strip():
        return {"contains": [], "cross": [], "verified": True}
    ingredients = fix_typos((ingredients or "").strip())
    label = fix_typos((allergy_label or "").strip())
    trailing = _TRAILING.search(label)
    if trailing:
        label = label[: trailing.start()].strip()

    contains: set[str] = set()
    cross: set[str] = set()
    stated = False  # 함유 표시 문장이 있는지 (19종이 아닌 성분이라도: '양고기 함유', '오이 함유')
    for segment in _SEGMENT_SPLIT.split(f"{ingredients}\n{label}"):
        if not segment or not segment.strip():
            continue
        # 한 구간에 '함유'와 혼입 문구가 섞여 있으면 어느 쪽인지 단정할 수 없으므로 함유로 본다 (안전 쪽 판정).
        is_cross = _CROSS_PATTERN.search(segment) and "함유" not in segment
        (cross if is_cross else contains).update(_match(segment))
        if not is_cross and _STATED.search(segment) and not _NOT_ALLERGY_NOTICE.search(segment):
            stated = True
    cross -= contains

    # '같은 제조시설' 문구만 있고 함유 표시가 없으면 판정 근거로 보지 않는다.
    # 라면처럼 실제 함유 성분(밀·대두 등)은 뒷면 이미지에만 있고 텍스트에는 혼입 문구만 실린 상품이 많다.
    # 이걸 '함유 없음'으로 보면 밀 알레르기에도 PASS가 되므로, 원재료명이나 '해당 없음' 표시가 없는 한 확인 필요로 둔다.
    # 단, '양고기 함유'처럼 함유 표시 문장이 있으면 표시대로 19종 함유가 없는 것으로 본다.
    verified = bool(ingredients) or bool(contains) or stated or bool(label and _NONE_PATTERN.search(label))
    return {"contains": sorted(contains), "cross": sorted(cross), "verified": verified}
