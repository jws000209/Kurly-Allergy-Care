"""OCR 글자 → 원재료명 · 함유 알레르기 · 같은 시설 제조 알레르기.

알레르겐 이름·오탈자 교정·혼입 문구 판별은 백엔드 Rule Engine과 같은 기준을 쓰도록
kurly-allergy-care/backend/app/extractor.py 를 그대로 가져다 쓴다.
"""
import re
import sys

import paths

sys.path.insert(0, str(paths.BACKEND))
from app.allergens import ALLERGEN_NAMES  # noqa: E402
from app.extractor import _CROSS_PATTERN, _match, fix_typos  # noqa: E402

# OCR이 '원재료명'을 '원자l료영', '원지|료명', '원새료영'처럼 깨뜨리므로 느슨하게 찾는다
_ING_START = re.compile(r"원\s?.{0,2}\s?료\s?[명영엉멍]?\s*(및\s*.{0,2}\s*[량랑당람])?\s*[:：]?")
# 원재료명 뒤에 오는 다른 표시 항목 — 여기서 원재료명이 끝난다
_ING_END = re.compile(
    r"식품\s*유형|내\s*용\s*량|제\s*조\s*원|제조사|제조\s*및|유통\s*기한|소비\s*기한|보관\s*방법|보존\s*(및|기준|방법)|"
    r"포장\s*재질|품목\s*(보고|제조)|영양\s*정보|업소\s*[명영]|소재지|판매원|반품|원산지|수입\s*(원|판매|자)|"
    r"알레르기|알러지|(이|본|해당)\s*제품은|부정\s*[·•ㆍ]?\s*불량|고객\s*(센터|상담|행복)|살균|멸균|가열처리|"
    r"[가-힣()]{1,20}\s*함유|※|1일\s*영양|구입처|상담|교환|소비자|나트[륨름]|탄수화물|트랜스\s*지방|단백질\s*[0-9]|지방\s*[0-9]|콜[레리]스테|영양\s*성분|kcal")
_ING_MAX = 500

# 문장 나누기: OCR은 줄이 문장 중간에서 끊기므로 줄바꿈을 먼저 이어 붙인 뒤 나눈다
_SENT_SPLIT = re.compile(r"[•●※■▶·ㆍ]|(?<=[다요음])\s*\.|\.\s|(?=(?:이|본|해당)\s*제품은)|(?<=함유)|(?<=가능)(?!성)|(?<=있음)")
# 함유 표시: '우유, 대두 함유' / '알레르기 유발물질: 우유, 대두' / '함유: 우유'
_CONTAINS = re.compile(r"[함힘]\s?[유우융윰]|유발\s*물질|유발\s*성분|알레르기\s*(표시|성분|정보)|알러지\s*(표시|성분|정보)")
_NOT_ALLERGY = re.compile(r"당알코올|페닐알라닌|카페인|알코올|나트륨|과량\s*섭취|비타민|칼슘|철분|식이섬유|단백질\s*함유")
_CROSS_EXTRA = re.compile(r"시[설셜]|혼\s?입|입\s?가능|같은\s?제조|동일\s?제조")
# 시설 문장에서 알레르겐 나열이 끝나는 곳
_CROSS_KEY = re.compile(r"사용\s?[한하]|(같은|동일한?)\s?(제조|시|공|라)|혼\s?입|입\s?가능|시[설셜]에서|제조\s?라인")


# OCR에서 자주 보는 오인식 (백엔드 오탈자 교정에 더해). '일'→'밀'은 성분 나열 안에서만 바꾼다
_OCR_TYPOS = [(re.compile(p), fix) for p, fix in [
    (r"(?<=[,、\s])일(?=\s*[,、를을와과및])", "밀"), (r"(?<=[,、\s])맡(?=\s*[,、])", "밀"),
    (r"달갈|딜걀|달길", "달걀"), (r"대뒤|대듀", "대두"), (r"땅공|땅쿵", "땅콩"), (r"호뒤|흐두", "호두"),
    (r"토마퇴|토미토", "토마토"), (r"복승아|복숭이|복숭어", "복숭아"), (r"돼지고가|돼지교기", "돼지고기"),
    (r"쇠고가|쇠교기", "쇠고기"), (r"닭고가|닭교기", "닭고기"), (r"메멀|매밀", "메밀"),
]]


def _flat(text: str) -> str:
    """줄을 이어 붙인다. 한글끼리 끊긴 줄('알\n류', '아\n황산류')은 띄우지 않고 붙인다."""
    text = re.sub(r"(?<=[가-힣])[ \t]*\n\s*(?=[가-힣])", "", text or "")
    text = re.sub(r"\s*\n\s*", " ", text)
    for p, fix in _OCR_TYPOS:
        text = p.sub(fix, text)
    return text


# ---- 좌표로 표 안의 '원재료명' 칸 읽기 (RapidOCR 상자 좌표) ----
# 표에서 왼쪽 열에 오는 항목 이름. 원재료명 칸의 위·아래 경계를 정한다 (짧은 줄에서만 찾는다)
_FIELD = re.compile(
    r"제품명|식품(의)?유형|내용량|품목(보고|제조)|포장재질|보관방법|보존|제조원|제조사|제조일|제조공장|유통|판매원|축산물|"
    r"고객|원산지|소비기한|업소|소재지|반품|영양정보|용도|섭취방법|주의사항|수입원|수입자|살균|멸균|가열|성분|규격")
_ING_LABEL = re.compile(r"원.{0,2}료|원재료|재료명|원료명")
_AND_AMOUNT = re.compile(r"^\s*및\s*.{0,1}\s*[함험암]\s*[량랑당람]\s*[:：]?")


def _cy(b):
    return (b[1] + b[3]) / 2


def _overlap_x(a, b) -> bool:
    return min(a[2], b[2]) - max(a[0], b[0]) > 0.3 * min(a[2] - a[0], b[2] - b[0])


def _is_field(line) -> bool:
    t = line["t"].replace(" ", "")
    return len(t) <= 14 and not re.search(r"[,%]", t) and bool(_FIELD.search(t))


def _stacked_label(lines):
    """'원/재/료/명' 이 한 글자씩 세로로 쌓인 칸 → 하나의 상자로 합친다."""
    out = []
    for l in lines:
        if l["t"].strip() != "원":
            continue
        h = max(8, l["b"][3] - l["b"][1])
        below = [m for m in lines if m["t"].strip() in ("재", "료", "명", "재료", "료명") and _overlap_x(m["b"], l["b"])
                 and 0 < m["b"][1] - l["b"][1] < 5 * h]
        if below:
            b = [min(x["b"][0] for x in [l] + below), l["b"][1], max(x["b"][2] for x in [l] + below),
                 max(x["b"][3] for x in below)]
            out.append({"t": "원재료명", "b": b})
    return out


def ingredients_from_lines(lines: list[dict]) -> str:
    """표 형식 라벨에서 '원재료명' 칸만 읽는다 (RapidOCR 줄 좌표 사용).
    글자만 이어 붙이면 OCR이 옆 칸 · 영양정보 표를 섞어 읽어 원재료명 사이에 끼어들기 때문.

    - 행 범위: 왼쪽 열에서 원재료명 위·아래에 있는 다른 항목 이름(제품명, 포장재질 …) 사이
      (항목 이름은 칸 높이의 가운데에 오므로 내용은 원재료명 글자보다 위에서 시작할 수 있다)
    - 표형 (이름 칸 | 내용 칸): 이름 상자 오른쪽의 줄들
    - 글 흐름형 ('원재료명 국산원유 100%…'가 한 상자로 읽히거나 내용이 이름 아래로 줄바꿈됨): 이름 뒤 글자 + 아래 줄들
    """
    best = ""
    labels = [l for l in lines if _ING_LABEL.search(l["t"].replace(" ", "")[:8])]
    for L in labels + _stacked_label(lines):
        lb = L["b"]
        lh = max(8, min(lb[3] - lb[1], 40))
        m = _ING_START.search(L["t"])
        after = L["t"][m.end():] if m else ""
        merged = len(re.findall(r"[가-힣]", after)) >= 2
        name_right = lb[2] if not merged else lb[0] + 4 * lh   # 이름 칸 오른쪽 끝 (한 상자면 이름 길이만큼)
        col = [l for l in lines if l is not L and _is_field(l) and l["b"][0] < name_right and l["b"][2] > lb[0]]
        above = [l["b"][3] for l in col if l["b"][3] <= lb[1] + 2]
        below = [l["b"][1] for l in col if l["b"][1] >= lb[3] - 2]
        top = max(above) if above else lb[1] - 2 * lh
        bot = (min(below) - lh / 2) if below else lb[3] + 8 * lh
        # 같은 행 오른쪽 줄, 이름 바로 아래에서 이름과 같은 x로 시작하는 줄 (줄바꿈되어 왼쪽으로 돌아온 내용)
        row_right = [l for l in lines if l is not L and l["b"][0] >= lb[2] - lh / 2 and abs(_cy(l["b"]) - _cy(lb)) < 0.7 * lh]
        under = [l for l in lines if l is not L and 0 <= l["b"][1] - lb[3] < 1.5 * lh and abs(l["b"][0] - lb[0]) < lh
                 and not _is_field(l) and _AND_AMOUNT.sub("", l["t"]).strip()]
        if merged or (row_right and under):
            # 글 흐름형: '원재료명 정제수, 오트…' 가 줄바꿈되어 이름 아래로 이어진다
            x_end = lb[2] if merged else max(l["b"][2] for l in row_right)
            span = x_end - lb[0]
            below_lines = [l for l in lines if l is not L and l not in row_right and _cy(l["b"]) > _cy(lb) + 0.5 * lh
                           and l["b"][1] < bot and lb[0] - lh <= l["b"][0] < lb[0] + 0.6 * span]
            ordered = ([] if merged else row_right) + below_lines
            pieces = ([after] if merged else []) + [_AND_AMOUNT.sub("", l["t"]) for l in
                                                    sorted(ordered, key=lambda l: (_cy(l["b"]), l["b"][0]))]
        else:
            # 표형: 이름 칸 오른쪽 내용 칸
            cand = [l for l in lines if l is not L and l["b"][0] >= lb[2] - lh / 2 and top - 2 <= _cy(l["b"]) <= bot]
            if not cand:
                continue
            # 이름은 칸 높이의 가운데에 온다 → 내용이 이름 아래로 내려간 만큼만 위로도 올라간다고 본다
            # (위 칸의 마지막 줄이 섞이지 않게)
            down = max([_cy(l["b"]) - _cy(lb) for l in cand if _cy(l["b"]) > _cy(lb)] + [0])
            cand = [l for l in cand if _cy(l["b"]) >= _cy(lb) - max(down, lh / 2) - 2]
            if not cand:
                continue
            # 오른쪽에 붙은 다른 표(영양정보 등)는 뺀다: 원재료명 칸은 왼쪽 정렬이라 줄 시작 x가 칸 왼쪽 근처다
            left = min(l["b"][0] for l in cand)
            right = max(l["b"][2] for l in cand if l["b"][0] <= left + 3 * lh)
            cand = [l for l in cand if l["b"][0] < left + 0.6 * (right - left)]
            pieces = [l["t"] for l in sorted(cand, key=lambda l: (_cy(l["b"]), l["b"][0]))]
        seg = _flat("\n".join(pieces))
        end = _ING_END.search(seg)
        seg = (seg[: end.start()] if end else seg).strip(" :：,·-")
        if len(re.findall(r"[가-힣]", seg)) >= 2 and len(seg) > len(best):
            best = seg[:_ING_MAX]
    return best


def ingredients(text: str) -> str:
    """원재료명 내용. 여러 개면 가장 긴 것 (표 안에서 OCR 순서가 뒤섞여 짧게 잘리는 경우가 많다)."""
    flat = _flat(text)
    best = ""
    for m in _ING_START.finditer(flat):
        rest = flat[m.end(): m.end() + _ING_MAX]
        end = _ING_END.search(rest)
        seg = rest[: end.start()] if end else rest
        seg = seg.strip(" :：,·-")
        if len(re.findall(r"[가-힣]", seg)) >= 2 and len(seg) > len(best):
            best = seg
    return best


def allergy_sentences(text: str):
    """(함유 알레르기, 같은 시설 알레르기, 근거 문장들)"""
    contains, cross, evidence = set(), set(), []
    for sent in _SENT_SPLIT.split(fix_typos(_flat(text))):
        if not sent or len(sent.strip()) < 2:
            continue
        is_cross = bool(_CROSS_PATTERN.search(sent) or _CROSS_EXTRA.search(sent))
        if is_cross and not re.search(r"함\s?유", sent):
            # 알레르겐은 '…를 사용한 제품과 같은 시설' / '… 혼입 가능'의 앞에 온다. 그 앞 300자만 보고,
            # '제품은' 뒤 또는 '원재료…' 뒤부터 본다 (앞에 붙어 온 원재료명이 시설 알레르기로 잡히지 않게)
            key = _CROSS_KEY.search(sent)
            part = sent[max(0, key.start() - 300): key.start()] if key else sent
            part = part.split("원재료")[-1]
            if "제품은" in part:   # '본 제품은 …' 이 여러 번 잡히면 처음 것 뒤부터 (OCR이 다른 문구를 중간에 끼워 넣음)
                part = part.split("제품은", 1)[1]
            found = _match(part)
            if found:
                cross |= found
                evidence.append("[시설] " + sent.strip()[:200])
        elif _CONTAINS.search(sent):
            # '함유' 바로 앞부분만 본다. 원재료명 끝('…%', '…]')이 붙어 오면 그 뒤부터 (원재료 전체가 함유로 잡히지 않게)
            m = re.search(r"[함힘]\s?[유우융윰]", sent)
            if m:
                before = re.split(r"[%\]]", sent[max(0, m.start() - 50): m.start()])[-1]
                # 함유 바로 앞 낱말이 '카페인', '칼슘' 같은 것이면 알레르기 표시가 아닌 주의 문구
                words = [w for w in re.split(r"[,()\s]+", before) if w]
                if words and _NOT_ALLERGY.search(words[-1]):
                    continue
                part = before + sent[m.start(): m.end()]
            else:
                part = sent
            found = _match(part)
            if found:
                contains |= found
                evidence.append("[함유] " + part.strip()[:200])
    cross -= contains
    return contains, cross, evidence


# 원재료다운 정도: 원재료명은 쉼표로 나열되고 '정제수·설탕·…추출물·%'가 많다. 영양정보·주소·안내 문구가 섞이면 깎는다
_ING_GOOD = re.compile(r"정제수|설탕|소금|정제염|식염|천일염|분말|추출물|농축|향료|전분|원유|분유|유크림|시럽|올리고당|"
                       r"혼합제제|가공품|산도조절제|유화제|증점제|감미료|색소|효소|밀가루|대두|국산|외국산|[가-힣]+산\)|%")
_ING_BAD = re.compile(r"kcal|[0-9]\s*mg|기준치|영양|상담|고객|구입처|반품|교환|소비기한|유통기한|보관|특별시|광역시|"
                      r"[가-힣]{1,4}[시군구]\s|[가-힣]+로\s?[0-9]|[가-힣]+길\s?[0-9]|[0-9]{3,4}-[0-9]{4}|www|http|"
                      r"사용한|시설|제조하|혼입|습니다|십시오|바랍니다|주시기|드세요|하세요")


def ingredient_score(seg: str) -> int:
    return seg.count(",") + len(_ING_GOOD.findall(seg)) - 3 * len(_ING_BAD.findall(seg))


def extract(texts: list[str], line_sets: list[list[dict]] | None = None) -> dict:
    """한 상품의 OCR 글자(이미지·판독 여러 개)에서 뽑는다. 알레르겐은 판독 결과를 합친다.
    line_sets: RapidOCR 줄 좌표 (이미지마다). 원재료명은 좌표로 칸을 골라 읽은 것도 후보에 넣는다."""
    contains, cross, evidence = set(), set(), []
    for t in texts:
        c, x, ev = allergy_sentences(t)
        contains |= c
        cross |= x
        evidence += ev
    cross -= contains
    # 원재료명: 좌표로 읽은 것과 글자에서 찾은 것 중 원재료다운 점수가 가장 높은 것 (같으면 좌표)
    cands = [(ingredients_from_lines(lines), "좌표") for lines in line_sets or []]
    cands += [(ingredients(t), "글자") for t in texts]
    scored = [(ingredient_score(seg), how == "좌표", seg, how) for seg, how in cands if seg]
    best = max(scored, default=None)
    ing, how = (best[2], best[3]) if best and best[0] > 0 else ("", "")
    order = {n: i for i, n in enumerate(ALLERGEN_NAMES)}
    return {
        "원재료명": ing,
        "원재료명_읽은방법": how,
        "원재료명_알레르겐": ", ".join(sorted(_match(fix_typos(ing)), key=order.get)) if ing else "",
        "함유_알레르기": ", ".join(sorted(contains, key=order.get)),
        "같은시설_알레르기": ", ".join(sorted(cross, key=order.get)),
        "근거문장": " | ".join(dict.fromkeys(evidence))[:1500],
    }
