"""Allergen Dictionary: 국내 알레르기 유발물질 표시대상 19종.

- name: 표준명 (프로필·Rule Engine에서 쓰는 코드)
- synonyms: 원재료명 안에서 부분 일치로 찾는 동의어·파생 원재료명
- exact: 토큰 전체가 일치할 때만 인정하는 단어 (한 글자라 부분 일치가 위험한 경우)
- masks: 매칭 전에 지우는 표현 (오탐 방지. 예: '메밀'의 '밀', '땅콩버터'의 '버터')
- aliases: 사용자가 대화·프로필에서 부르는 이름
"""
import re

ALLERGENS = [
    {
        "name": "알류",
        "aliases": ["알류", "달걀", "계란", "난류", "메추리알"],
        "synonyms": ["알류", "난류", "달걀", "계란", "난백", "난황", "전란", "메추리알", "마요네즈"],
    },
    {
        "name": "우유",
        "aliases": ["우유", "유제품", "밀크"],
        "synonyms": ["우유", "원유", "분유", "유청", "카제인", "유당", "버터", "치즈", "크림",
                     "연유", "요거트", "요구르트", "밀크", "산양유"],  # 산양유: 19종은 아니지만 우유와 교차반응이 흔해 안전 쪽으로
        "masks": ["코코넛밀크", "아몬드밀크", "오트밀크", "라이스밀크", "소이밀크", "땅콩버터",
                  "피넛버터", "코코아버터", "카카오버터", "시어버터", "코코넛크림"],
    },
    {"name": "메밀", "aliases": ["메밀"], "synonyms": ["메밀"]},
    {"name": "땅콩", "aliases": ["땅콩", "피넛"], "synonyms": ["땅콩", "피넛"]},
    {
        "name": "대두",
        "aliases": ["대두", "콩", "두유"],
        "synonyms": ["대두", "두유", "두부", "유부", "간장", "된장", "고추장", "춘장", "쌈장",
                     "청국장", "낫토", "콩"],
        "masks": ["땅콩", "완두콩", "강낭콩", "병아리콩", "렌틸콩", "커피콩",
                  "수유부"],  # '임산부 및 수유부는…' 주의 문구의 '유부' 오탐 (10/07 v2 데이터)
    },
    {
        "name": "밀",
        "aliases": ["밀", "밀가루", "글루텐"],
        "synonyms": ["밀", "소맥분", "글루텐", "세몰리나", "빵가루", "부침가루", "튀김가루"],
        "masks": ["메밀", "밀크", "밀키트", "오트밀", "밀감", "밀봉"],
    },
    {"name": "고등어", "aliases": ["고등어"], "synonyms": ["고등어"]},
    {
        "name": "게",
        "aliases": ["게", "꽃게", "대게"],
        "synonyms": ["꽃게", "대게", "홍게", "킹크랩", "크랩", "게살", "게추출", "게분말",
                     "게엑기스", "게농축", "갑각류"],  # '갑각류'는 게·새우를 함께 가리킨다
        "exact": ["게"],
    },
    {"name": "새우", "aliases": ["새우"], "synonyms": ["새우", "쉬림프", "대하", "갑각류"]},
    {
        "name": "돼지고기",
        "aliases": ["돼지고기", "돼지"],
        "synonyms": ["돼지", "돈육", "돈지", "돈골", "한돈", "베이컨", "라드"],
    },
    {"name": "복숭아", "aliases": ["복숭아"], "synonyms": ["복숭아", "황도", "백도", "천도", "피치"]},
    {"name": "토마토", "aliases": ["토마토"], "synonyms": ["토마토", "케첩", "케찹"]},
    {
        "name": "아황산류",
        "aliases": ["아황산류", "아황산"],
        "synonyms": ["아황산", "이산화황"],
    },
    {"name": "호두", "aliases": ["호두"], "synonyms": ["호두"]},
    {"name": "닭고기", "aliases": ["닭고기", "닭"], "synonyms": ["닭", "치킨", "계육"]},
    {
        "name": "쇠고기",
        "aliases": ["쇠고기", "소고기"],
        "synonyms": ["쇠고기", "소고기", "우육", "한우", "육우", "비프", "사골", "우지"],
    },
    {"name": "오징어", "aliases": ["오징어"], "synonyms": ["오징어", "한치"]},  # 한치는 오징어류
    {
        "name": "조개류",
        "aliases": ["조개류", "조개", "굴", "전복", "홍합"],
        "synonyms": ["조개", "굴", "전복", "홍합", "바지락", "가리비", "재첩", "꼬막"],
        "masks": ["굴비"],
    },
    {"name": "잣", "aliases": ["잣"], "synonyms": ["잣"]},
]

ALLERGEN_NAMES = [a["name"] for a in ALLERGENS]

# 사용자 표현 → 표준명. 긴 표현을 먼저 대조해야 '메밀'이 '밀'로 잡히지 않는다.
_ALIAS_TO_NAME = sorted(
    ((alias, a["name"]) for a in ALLERGENS for alias in a["aliases"]),
    key=lambda pair: -len(pair[0]),
)


def normalize_allergen(term: str) -> str | None:
    """사용자·LLM이 말한 성분명을 19종 표준명으로 바꾼다. 19종 밖이면 None."""
    term = term.strip()
    if term in ALLERGEN_NAMES:
        return term
    for alias, name in _ALIAS_TO_NAME:
        if alias == term:
            return name
    return None


_PARTICLES = {"", "와", "과", "랑", "이랑", "하고", "도", "은", "는", "이", "가", "을", "를", "류"}


def split_allergens(phrase: str) -> tuple[list[str], str]:
    """자연어 구절("간식 우유·달걀")에서 알레르겐 표준명과 나머지 낱말을 나눈다.

    '에게'의 '게'처럼 낱말 일부가 걸리지 않도록 낱말 단위(조사 허용)로만 대조한다.
    """
    found: list[str] = []
    rest: list[str] = []
    for token in re.split(r"[\s·,/]+", phrase):
        for alias, name in _ALIAS_TO_NAME:
            if token.startswith(alias) and token[len(alias):] in _PARTICLES:
                if name not in found:
                    found.append(name)
                break
        else:
            rest.append(token)
    return found, " ".join(rest)
