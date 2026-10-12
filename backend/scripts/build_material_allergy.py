"""재료 기반 알레르기 열 만들기 (추천 알고리즘 확인용 데이터, 판정 아님)

- 원재료명: 알레르겐_원재료_매칭사전_v1.xlsx 의 자동매칭 Y + 신뢰등급 A 행만 배정한다.
  B/C 등급이나 자동매칭 N 행에 걸린 것은 배정하지 않고 '검토후보'로만 남긴다.
- 제조시설: 입력 CSV의 제조시설 열(이미 19종 표준명)을 그대로 쓴다.
- 확장 20종(구분 EXPANDED)은 한국 표시 의무 대상이 아니므로 별도 열에 둔다.
- 알레르기 판정은 Rule Engine이 한다. 이 파일은 데이터 구축용이다.

사용: python build_material_allergy.py [입력CSV] [출력CSV] [원본 kurly_products_v2.csv]
"""
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]  # 핵심프로젝트 AI
DATA = ROOT / "마켓컬리 데이터"
DICT = ROOT / "kurly-allergy-care" / "data" / "알레르겐_원재료_매칭사전_v1.xlsx"

# 엑셀 헤더가 깨져 있어 이름이 아니라 위치로 읽는다 (사전 '원재료_매칭사전' 시트)
C_ID, C_KIND, C_TERM, C_KEY, C_RULE, C_AUTO, C_GRADE = 1, 4, 6, 9, 10, 11, 12

# CLAUDE.md 의 19종 표기명 (알레르겐ID 순서)
STD19 = {
    "A001": "알류", "A002": "우유", "A003": "메밀", "A004": "땅콩", "A005": "대두",
    "A006": "밀", "A007": "고등어", "A008": "게", "A009": "새우", "A010": "돼지고기",
    "A011": "복숭아", "A012": "토마토", "A013": "아황산류", "A014": "호두", "A015": "닭고기",
    "A016": "쇠고기", "A017": "오징어", "A018": "조개류", "A019": "잣",
}
NAME19 = set(STD19.values())

SPLIT = re.compile(r"[^0-9A-Za-z가-힣]+")


def norm(s):
    return re.sub(r"\s+", "", str(s)).lower()


def tokens(text):
    return [norm(t) for t in SPLIT.split(str(text)) if norm(t)]


def load_dictionary():
    """(키, 규칙, 표준명, 구분, 자동배정 여부, 원문) 목록을 만든다."""
    m = pd.read_excel(DICT, sheet_name="원재료_매칭사전", dtype=str)
    names = {}
    ids = pd.read_excel(DICT, sheet_name="39_알레르겐_마스터", dtype=str, header=0)
    for _, r in ids.iterrows():
        names[r.iloc[0]] = STD19.get(r.iloc[0], str(r.iloc[1]))
    rules = []
    for _, r in m.iterrows():
        aid = r.iloc[C_ID]
        auto = (r.iloc[C_AUTO] == "Y") and (r.iloc[C_GRADE] == "A")
        rules.append({
            "key": norm(r.iloc[C_KEY]),
            "rule": r.iloc[C_RULE],
            "name": names.get(aid, aid),
            "kind": r.iloc[C_KIND],
            "auto": auto,
            "term": str(r.iloc[C_TERM]),
        })
    return rules


def match(text, rules):
    """원재료 텍스트에서 규칙별로 걸린 (표준명, 구분, 자동여부, 원문) 집합을 돌려준다."""
    hits = set()
    toks = tokens(text)
    for rl in rules:
        if not rl["key"]:
            continue
        for t in toks:
            ok = (t == rl["key"]) if rl["rule"] == "EXACT" else (rl["key"] in t)
            if ok:
                hits.add((rl["name"], rl["kind"], rl["auto"], rl["term"]))
                break
    return hits


def main(src=None, out=None, orig=None):
    src = Path(src) if src else DATA / "kurly_products_v2_material_facility.csv"
    out = Path(out) if out else DATA / "kurly_products_v2_material_allergy.csv"
    rules = load_dictionary()
    df = pd.read_csv(src, dtype={"product_id": str}, encoding="utf-8")
    raw_col, fac_col = df.columns[-2], df.columns[-1]  # 원재료명, 제조시설 (헤더 깨짐 → 위치)

    res = {k: [] for k in ["원재료_알레르기", "원재료_확장", "원재료_검토후보", "원재료_상태",
                           "시설_알레르기", "시설_상태", "검토필요", "매칭근거"]}
    for raw, fac in zip(df[raw_col], df[fac_col]):
        hits = match(raw, rules) if pd.notna(raw) else set()
        auto19 = sorted({n for n, k, a, _ in hits if a and k == "MFDS"})
        autox = sorted({n for n, k, a, _ in hits if a and k == "EXPANDED"})
        review = sorted({n for n, k, a, _ in hits if not a} - set(auto19) - set(autox))
        ev = sorted({f"{t}→{n}" for n, k, a, t in hits if a})
        res["원재료_알레르기"].append("|".join(auto19))
        res["원재료_확장"].append("|".join(autox))
        res["원재료_검토후보"].append("|".join(review))
        res["원재료_상태"].append("정보없음" if pd.isna(raw) else "있음")
        if pd.isna(fac):
            res["시설_알레르기"].append(None)
            res["시설_상태"].append("정보없음")
        else:
            fl = [x.strip() for x in str(fac).split(",") if x.strip()]
            res["시설_알레르기"].append("|".join(x for x in fl if x in NAME19))
            res["시설_상태"].append("있음")
        res["검토필요"].append(bool(review) or pd.isna(raw))
        res["매칭근거"].append(", ".join(ev))
    for k, v in res.items():
        df[k] = v
    df.to_csv(out, index=False, encoding="utf-8")
    print("저장:", out, len(df), "행")
    print("원재료 정보없음:", (df["원재료_상태"] == "정보없음").sum(),
          "| 시설 정보없음:", (df["시설_상태"] == "정보없음").sum(),
          "| 검토필요:", df["검토필요"].sum())

    if orig:
        check(df, Path(orig))


def check(df, orig_path):
    """kurly 공식 allergy_info 의 '함유' 문구와 대조해 원재료 기반 열의 품질을 본다.

    원재료명이 있고 allergy_info 가 있는 상품만 비교한다 (원재료가 없으면 비교 불가).
    """
    o = pd.read_csv(orig_path, dtype={"product_id": str}, encoding="utf-8")
    o = o[["product_id", "allergy_info"]].rename(columns={"allergy_info": "_ai"})
    m = df.merge(o, on="product_id", how="left")
    m = m[(m["원재료_상태"] == "있음") & m["_ai"].notna()]
    tp = fp = fn = 0
    for info, mine in zip(m["_ai"], m["원재료_알레르기"]):
        ham = {x for line in str(info).splitlines() if "함유" in line for x in NAME19 if x in line}
        ours = set(str(mine).split("|")) - {"", "nan"}
        tp += len(ham & ours); fp += len(ours - ham); fn += len(ham - ours)
    p = tp / (tp + fp) if tp + fp else 0
    r = tp / (tp + fn) if tp + fn else 0
    print(f"대조 상품 {len(m)}개 | 정밀도 {p:.1%} | 재현율 {r:.1%} (kurly 함유 문구 기준)")


if __name__ == "__main__":
    args = sys.argv[1:]
    main(*(args + [None] * (3 - len(args))))
