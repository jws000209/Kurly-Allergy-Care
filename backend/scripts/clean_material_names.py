"""원재료명 정제: 직접 판독 결과 우선, 나머지는 OCR 잡음 제거 + 오탈자 교정. 추천 확인용 데이터.

입력: kurly_products_v2_material_facility.csv (원재료명 = 끝에서 두 번째 열, 헤더가 깨져 위치로 읽는다)
직접 판독: 알레르기판독_파트1/work/vision_part1.jsonl, 알레르기 파트 모으기/vision_part4.jsonl
출력 (마켓컬리 데이터/):
  kurly_products_v2_material_facility_clean.csv  원재료명_정제·원재료명_출처·원재료명_삭제사유 열 추가
  원재료명_정제_로그.csv                           바뀐 항목 전체 (검토용)

규칙
1) 직접 판독이 있으면 그 원재료명을 쓴다 (출처 '직접판독').
   직접 판독에서 원재료명이 없다고 한 배너·사진은 빈 값으로 둔다.
2) 나머지 OCR 원문은 쉼표·마침표 단위 구절로 나눠 잡음 구절을 버린다.
   보관·영양성분·표시일·용기·살균·주소·전화·보고번호·긴 숫자·깨진 한자 등이 든 구절.
   남은 구절이 하나도 없으면 원재료명을 삭제하고 사유를 남긴다 (출처 'OCR정제' → 삭제).
3) 오탈자: app.extractor.fix_typos (이미 쓰는 교정) → 알려진 OCR 오기 표 → 사전 낱말·직접 판독 낱말과
   한 글자만 다른 낱말 교정 (그 낱말이 충분히 많이 나올 때만). 모든 변경은 로그에 남긴다.

주의: OCR 원문은 깨진 글자가 많아 전부 복원되지 않는다. 자동 교정은 '확실한 것'만 하고 나머지는 그대로 둔다.
"""
import csv
import re
import sys
from collections import Counter
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

from app.extractor import fix_typos  # noqa: E402
from build_material_allergy import load_dictionary, match, NAME19  # noqa: E402

ROOT = BACKEND.parents[1]
DATA = ROOT / "마켓컬리 데이터"
SRC = DATA / "kurly_products_v2_material_facility.csv"
OUT = DATA / "kurly_products_v2_material_facility_clean.csv"
LOG = DATA / "원재료명_정제_로그.csv"
VISION = [
    ROOT / "마켓컬리 데이터" / "알레르기판독_파트1" / "work" / "vision_part1.jsonl",
    DATA / "알레르기 파트 모으기" / "vision_part4.jsonl",
]

# 구절 단위 잡음 (하나라도 들어 있으면 그 구절은 원재료가 아니다)
NOISE = re.compile(
    r"표시일|보관|영양|kcal|용기|주의|www|http|전화|주소|살균|보고번호|고객센터|소비자|알레르기\s*성분|"
    r"공장|천안|제조원|수입원|판매원|유통전문|유통기한|소비기한|포장|재질|원산지|내용량|1399|부정|불량|"
    r"반품|교환|cfu|식품유형|품목|제조사|분쟁|해결|냉장|냉동|개봉|섭취|드시|먹기|드십|바랍니다|"
    r"습니다|있습니다|포화지방|트랜스지방|콜레스테롤|나트륨\s*\d|탄수화물|단백질|당류\s*\d|지방\s*\d|"
    r"\d+(\.\d+)?\s*(g|mg|kcal|ml|mL|%|cfu)\b|\d{6,}|\(\s*\d{2,}\s*\)|[一-鿿]|"
    r"우리 제품|본 제품|이 제품|재활용|OTHER|PP\b|PET\b|ALLERGY|ALLERGEN",
    re.IGNORECASE,
)
# 오탈자 표 (관찰된 OCR 오기). 긴 것부터 적용한다.
KNOWN_TYPOS = [
    ("먹아물였", "물엿"), ("먹아물엿", "물엿"), ("물옛", "물엿"), ("물여", "물엿"),
    ("고츠가루", "고춧가루"), ("고츠", "고추"), ("참께", "참깨"), ("검정께", "검정깨"),
    ("나트름", "나트륨"), ("개나다", "캐나다"), ("일가루", "밀가루"),
    ("정재수", "정제수"), ("정제 소금", "정제소금"),
]
TOKEN = re.compile(r"[가-힣A-Za-z]{2,}")
SPLIT = re.compile(r"[,，;；\n]|\.\s")


def clean_ocr(text):
    """OCR 원문 → (정제 문자열, 버린 구절 수). 남는 구절이 없으면 ''."""
    kept, dropped = [], 0
    for seg in SPLIT.split(text):
        seg = seg.strip(" .-·/|[](){}")
        if not seg:
            continue
        if NOISE.search(seg) or not re.search(r"[가-힣A-Za-z]", seg):
            dropped += 1
            continue
        kept.append(seg)
    return ", ".join(kept), dropped


def load_vision():
    """상품별 직접 판독 1건 (판독불가가 아니고 원재료명이 가장 긴 것)."""
    import json
    best = {}
    for path in VISION:
        for line in open(path, encoding="utf-8"):
            r = json.loads(line)
            pid = r["file"].split("/")[1]
            name = r.get("원재료명")
            score = (0 if r.get("판독불가") else 1, len(name or ""))
            if pid not in best or score > best[pid][0]:
                best[pid] = (score, name)
    return {pid: name for pid, (_, name) in best.items()}


def build_vocab(vision_names, ocr_names):
    """교정에 쓸 낱말 빈도: 직접 판독 원재료명 + OCR 원문 전체 + 사전 한글 매칭어.

    OCR 오기는 드물게 나오고 실제 낱말은 많이 나오므로, 원문 전체에서 자주 나오는 낱말은 '실제 낱말'로 보고 건드리지 않는다.
    """
    freq = Counter()
    for name in list(vision_names) + list(ocr_names):
        freq.update(t for t in TOKEN.findall(name or "") if re.fullmatch(r"[가-힣]+", t))
    for rl in load_dictionary():
        if re.fullmatch(r"[가-힣]+", rl["key"] or ""):
            freq[rl["key"]] += 5
    return freq


def lev1(a, b):
    """편집거리 1 이하인지 (삽입·삭제·치환)."""
    if a == b:
        return False
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    s, t = (a, b) if len(a) < len(b) else (b, a)
    i = 0
    while i < len(s) and s[i] == t[i]:
        i += 1
    return s[i:] == t[i + 1:]


def correct(text, freq, changes):
    text = fix_typos(text)
    for bad, good in KNOWN_TYPOS:
        if bad in text and bad != good:
            text = text.replace(bad, good)
            changes.append(f"{bad}→{good}")
    good_words = [w for w, c in freq.items() if c >= 3]

    def fix_token(m):
        tok = m.group(0)
        # 실제 낱말(자주 나오는 말)과 짧은 낱말은 건드리지 않는다 ('굴소스'의 '굴'을 지우는 식의 오탐 방지)
        if not re.fullmatch(r"[가-힣]{3,}", tok) or freq.get(tok, 0) >= 30:
            return tok
        cands = [w for w in good_words if len(w) == len(tok) and lev1(tok, w)]  # 한 글자 치환만
        if len(cands) == 1 and freq[cands[0]] >= 20 * max(1, freq.get(tok, 0)):
            changes.append(f"{tok}→{cands[0]}")
            return cands[0]
        return tok

    return TOKEN.sub(fix_token, text)


def main():
    vision = load_vision()
    rows = list(csv.reader(open(SRC, encoding="utf-8-sig", newline="")))
    raw_names = [r[-2] for r in rows[1:] if r[-2]]
    vocab = build_vocab(vision.values(), raw_names)
    header, body = rows[0], rows[1:]
    base = header[:-2]
    raw_i = len(header) - 2
    pid_i = header.index("product_id")
    stats = Counter()
    log = []
    out_rows = []
    for r in body:
        pid, raw = r[pid_i], r[raw_i]
        src, cleaned, reason, changes = "", "", "", []
        if pid in vision:
            cleaned = vision[pid] or ""
            src = "직접판독" if cleaned else "직접판독(원재료 없음)"
        elif raw:
            text, dropped = clean_ocr(raw)
            stats["구절버림"] += dropped
            if text:
                cleaned = correct(text, vocab, changes)
                src = "OCR정제"
            else:
                src, reason = "삭제", "잡음 구절만 있음(원재료와 무관)"
        else:
            src = "원재료 없음"
        if src in ("직접판독", "OCR정제") and cleaned and raw and cleaned != raw:
            log.append([pid, src, raw[:200], cleaned[:200], ";".join(changes)])
        stats[src] += 1
        out_rows.append(r[:raw_i] + [cleaned, raw, src, reason, ";".join(changes), r[raw_i + 1]])
    out_header = base + ["원재료명_정제", "원재료명_원본", "원재료명_출처", "원재료명_삭제사유", "원재료명_교정"] + [header[-1]]
    with open(OUT, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(out_header)
        w.writerows(out_rows)
    with open(LOG, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["product_id", "출처", "원본", "정제", "교정"])
        w.writerows(log)
    print("출처별:", dict(stats))
    print(f"교정 로그 {len(log)}건 → {LOG}")
    return out_rows


if __name__ == "__main__":
    main()
