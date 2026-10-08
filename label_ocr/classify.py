"""판정 규칙: 원재료(신선) 상품 가려내기, 표시사항 3가지(원재료명 · 알레르기 함유 · 같은 시설 제조) 문구 찾기."""
import re
from functools import lru_cache

import pandas as pd

import paths

# OCR이 글자를 자주 깨뜨려서(원1료명, 원재 료 등) 공백·줄바꿈을 없앤 뒤 느슨하게 찾는다
ALLERGEN = (r"(우유|유제품|대두|콩|밀|땅콩|메밀|알류|난류|계란|달걀|가금류|메추리|고등어|게|새우|돼지고기|쇠고기|소고기|"
            r"닭고기|오징어|조개|굴|전복|홍합|복숭아|토마토|호두|잣|아황산|아몬드|캐슈|견과|갑각류|생선|어류|조개류|연체)")
PAT = {
    # 원재료명(및 함량): 깨진 표기 '원자l료영', '원지|료명', '원새료영', '원까료' 와 '및 함량' 쪽도 본다
    "원재료명": re.compile(r"원.{0,2}료[명영엉멍]|원재료|원료명|재료명|및함량|및험람|및함랑|및암당|및함당"),
    # 알레르기 함유: '알레르기'만으로는 같은 시설 문구와 겹치므로, 알레르겐 가까이의 '함유' 또는 유발물질 표시만 인정
    "알레르기함유": re.compile(ALLERGEN + r".{0,25}?(함유|함우|힘유)|(함유|함우)[:：]?" + ALLERGEN
                         + r"|유발물질|알레르기표시|알레르기정보|알레르기성분|알러지정보|알러지성분|알레르기유발성분"),
    "같은시설": re.compile(r"시설에서|제조시설|생산시설|같은.{0,3}시설|동일.{0,3}시설|시[설셜]에서제조|"
                       r"혼입|입가능|사용한제품|제조라인|생산라인|같은공장|동일공장|같은.{0,3}라인"),
}

# 원재료(신선) 상품: 아래 소분류 + 상품명에 가공 표시가 없는 것
RAW_SUB = {
    "채소": None,
    "과일·견과·쌀": {"쌀·잡곡", "제철과일", "수입과일", "친환경", "국산과일", "간편과일"},
    "정육·가공육·달걀": {"국내산 소고기", "국내산 돼지고기", "닭·오리고기", "수입산 소고기", "수입산 돼지고기·양고기"},
    "수산·해산·건어물": {"생선류", "제철수산", "해산물·전복·조개류", "새우·게·랍스터", "오징어·낙지·문어", "굴비·반건류"},
}
# 원재료 소분류 안에서 가공품을 가려내는 상품명 표현. '등심 구이·불고기·장조림·찜갈비·볶음탕용'처럼
# 손질 용도만 적은 생고기는 원재료로 보도록 용도 낱말은 넣지 않고, 양념·훈제·가공 표시만 본다
PROCESSED_NAME = re.compile(
    r"훈제|훈연|소스|양념|간장|매콤|직화|꼬치|닭갈비|무침|절임|장아찌|피클|가공|소시지|햄(?!프)|만두|튀김(?!용)|까스(?!용)|"
    r"커틀릿|통살|너겟|패티|떡갈비|국밥|주스(?!용)|즙(?!용)|칩|말랭이|시즈닝|마리네|바베큐|BBQ|밀키트|도시락|김치|젓갈|액젓|"
    r"어묵|맛살|스낵|크런치|분말|가루|퓨레|퓌레|즉석|데워|크림|버터(?!헤드|플라이)|치즈|덮밥|비빔|자반|반건조|말린|"
    r"굴비|동결건조|조미|볶은|볶음콩|소금구이")


def is_raw(category, subcategory, name) -> bool:
    subs = RAW_SUB.get(category, False)
    if subs is False or (subs is not None and subcategory not in subs):
        return False
    return not PROCESSED_NAME.search(str(name))


@lru_cache(maxsize=1)
def raw_product_ids() -> frozenset:
    prod = pd.read_csv(paths.PRODUCTS, dtype={"product_id": str})
    return frozenset(prod.loc[[is_raw(c, s, n) for c, s, n in zip(prod["category"], prod["subcategory"], prod["product_name"])],
                              "product_id"])


def flags(text: str) -> dict:
    """OCR 글자에서 3가지 문구가 보이는지 {이름: bool}"""
    norm = re.sub(r"\s+", "", text or "")
    return {k: bool(p.search(norm)) for k, p in PAT.items()}
