"""원재료명이 없는 상품 중 원물(표시 없이 그 자체인 식품)을 표시한다.

기준 (작업내용 3-19·3-28의 원물 판단을 소분류로 옮김):
- 원재료명이 없거나 잡음만 있어 삭제된 상품 (원재료명_출처 = '원재료 없음' 또는 '삭제')
- 소분류가 원물 목록에 있으면 '원물'로 표시
- 상품명에 39종 사전 성분이 들어 있으면(예: 달걀, 바지락, 새우, 고등어) 그 알레르기를 원물에 연결
  상품명 매칭은 사전 매칭 규칙을 그대로 쓴다. 원물 목록 밖 상품은 손대지 않는다.

출력: 마켓컬리 데이터/kurly_products_v2_material_facility_clean.csv 에 열 추가
  원물여부 ('원물' 또는 빈 값), 원물_이름_알레르겐 (상품명에서 찾은 39종, '|' 구분)
"""
import csv
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import re  # noqa: E402

# 원물 상품명의 알레르겐 낱말 → 표준명. 단어가 이 낱말로 '끝나는' 경우만 인정한다
# (예: '천도복숭아'→복숭아, '꽃게'→게). 중간에 낱말이 들어 있는 경우(게이샤, 밀크티, 넉넉하게, 땅콩호박)는 제외.
RAW_NAME_MAP = {
    "달걀": "알류", "계란": "알류",
    "바지락": "조개류", "꼬막": "조개류", "굴": "조개류", "전복": "조개류", "홍합": "조개류",
    "가리비": "조개류", "소라": "조개류", "골뱅이": "조개류", "조개": "조개류",
    "새우": "새우", "꽃게": "게", "게": "게", "고등어": "고등어", "오징어": "오징어",
    "쇠고기": "쇠고기", "소고기": "쇠고기", "돼지고기": "돼지고기", "닭고기": "닭고기",
    "복숭아": "복숭아", "토마토": "토마토", "사과": "사과", "셀러리": "셀러리", "겨자": "겨자",
    "연어": "연어", "키위": "키위", "바나나": "바나나", "오렌지": "오렌지", "파인애플": "파인애플",
    "아몬드": "아몬드", "호두": "호두", "잣": "잣", "땅콩": "땅콩", "참마": "참마", "산마": "참마",
}
_TOKEN = re.compile(r"[가-힣A-Za-z]+")


def name_allergens(product_name):
    found = set()
    for tok in _TOKEN.findall(product_name or ""):
        for kw, std in RAW_NAME_MAP.items():
            # 두 글자 이하 낱말(게·굴·잣 등)은 단어가 그대로 같을 때만 본다 ('넉넉하게'의 '게' 방지)
            if tok == kw or (len(kw) > 2 and tok.endswith(kw)):
                found.add(std)
    return found

CLEAN = BACKEND.parents[1] / "마켓컬리 데이터" / "kurly_products_v2_material_facility_clean.csv"

# 원물 소분류 (category > subcategory). 가공식품·조미·곡물차·액상차·유제품 가공은 뺀다.
RAW_CATEGORIES = {"채소"}  # 채소 대분류는 전부 원물
RAW_SUBCATEGORIES = {
    "과일·견과·쌀 > 쌀·잡곡", "과일·견과·쌀 > 제철과일", "과일·견과·쌀 > 수입과일",
    "과일·견과·쌀 > 친환경", "과일·견과·쌀 > 국산과일", "과일·견과·쌀 > 냉동·건과일",
    "생수·음료 > 생수/얼음",
    "커피·차 > 원두커피", "커피·차 > 드립백/커피백", "커피·차 > 콜드브루",
    "커피·차 > 허브차/꽃차/과일차", "커피·차 > 홍차/녹차/보이차",
    "정육·가공육·달걀 > 국내산 소고기", "정육·가공육·달걀 > 닭·오리고기",
    "정육·가공육·달걀 > 국내산 돼지고기", "정육·가공육·달걀 > 수입산 돼지고기·양고기",
    "정육·가공육·달걀 > 수입산 소고기",
    "수산·해산·건어물 > 생선류", "수산·해산·건어물 > 해산물·전복·조개류",
    "수산·해산·건어물 > 제철수산", "수산·해산·건어물 > 새우·게·랍스터",
}


def main():
    with open(CLEAN, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    header, body = rows[0], rows[1:]
    if "원물여부" in header:  # 다시 실행해도 열이 늘지 않도록 기존 열을 지운다
        keep = [i for i, c in enumerate(header) if c not in ("원물여부", "원물_이름_알레르겐")]
        header = [header[i] for i in keep]
        body = [[r[i] for i in keep] for r in body]
    ci, si = header.index("category"), header.index("subcategory")
    st = header.index("원재료명_출처")
    name_i = header.index("product_name")
    add = ["원물여부", "원물_이름_알레르겐"]
    out = [header + add]
    n_raw = n_allergen = 0
    for r in body:
        cat, sub = r[ci].strip(), r[si].strip()
        no_label = r[st] in ("원재료 없음", "삭제")
        raw_flag = no_label and (cat in RAW_CATEGORIES or f"{cat} > {sub}" in RAW_SUBCATEGORIES)
        if raw_flag:
            names = sorted(name_allergens(r[name_i]))
            out.append(r + ["원물", "|".join(names)])
            n_raw += 1
            n_allergen += bool(names)
        else:
            out.append(r + ["", ""])
    with open(CLEAN, "w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerows(out)
    print(f"원물 표시 {n_raw}개 (그중 상품명에 알레르겐 있음 {n_allergen}개)")


if __name__ == "__main__":
    main()
