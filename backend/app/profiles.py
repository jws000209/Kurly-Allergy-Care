"""가족 프로필 별칭과 대상 매칭.

누구를 대상으로 하느냐가 곧 어떤 알레르기를 피하느냐이므로, 프로필 연결은 LLM이 아니라
이 모듈의 규칙으로 정한다. LLM은 문장에서 "누구를 말했는지"(예: '큰애', '아들')만 뽑는다.

- 별칭 = 프로필 이름 + 이름의 낱말 + 관계·순서에서 만든 호칭 + 사용자가 직접 넣은 별칭
- 한 표현이 여러 프로필에 맞거나(애매), 아무 프로필에도 안 맞으면(없음) 추천을 멈추고 되묻는다.
"""
import re

RELATIONS = ["본인", "아빠", "엄마", "아들", "딸", "할아버지", "할머니", "기타"]
ORDERS = ["첫째", "둘째", "셋째", "막내"]

# 관계별 호칭
RELATION_WORDS = {
    "본인": ["본인", "나", "내꺼", "제꺼"],
    "아빠": ["아빠", "아버지", "남편", "애아빠"],
    "엄마": ["엄마", "어머니", "아내", "와이프", "애엄마"],
    "아들": ["아들", "아들내미"],
    "딸": ["딸", "딸내미"],
    "할아버지": ["할아버지", "할아범", "할부지"],
    "할머니": ["할머니", "할매"],
    "기타": [],
}
CHILD_RELATIONS = {"아들", "딸"}
# 순서 + 관계로 만드는 호칭
ORDER_WORDS = {
    "첫째": {"any": ["첫째", "큰애", "첫애", "맏이"], "아들": ["큰아들", "장남", "첫째아들"], "딸": ["큰딸", "장녀", "첫째딸"]},
    "둘째": {"any": ["둘째", "작은애", "둘째애"], "아들": ["작은아들", "둘째아들", "차남"], "딸": ["작은딸", "둘째딸", "차녀"]},
    "셋째": {"any": ["셋째"], "아들": ["셋째아들"], "딸": ["셋째딸"]},
    "막내": {"any": ["막내"], "아들": ["막내아들"], "딸": ["막내딸"]},
}
# 아이를 두루 가리키는 말. 아이 프로필이 하나면 그 아이, 여럿이면 되묻는다 ('아이들'·'애들'은 모두).
CHILD_SINGULAR = ["아이", "애기", "아기", "꼬마"]
CHILD_PLURAL = ["아이들", "애들", "아이둘", "아이셋"]
# 여러 명을 묶어 부르는 말 → 해당하는 프로필을 모두 대상으로 (알레르기는 합집합). 되묻지 않는다.
GROUP_WORDS = {
    "children": ["자식", "자식들", "자녀", "자녀들", "애들", "아이들", "아이둘", "아이셋", "애기들", "꼬맹이들"],
    "아들": ["아들들"],
    "딸": ["딸들"],
    "parents": ["부모", "부모님", "엄마아빠", "아빠엄마", "엄빠"],
    "grandparents": ["조부모", "조부모님", "할머니할아버지", "할아버지할머니", "할부모"],
    "all": ["가족", "가족들", "온가족", "우리가족", "식구", "식구들", "다같이", "모두", "다들"],
}


def relation_of(member: dict) -> str:
    """관계. 비어 있으면 이름에서 짐작한다 ('둘째 아들' → 아들, '엄마' → 엄마).

    관계·순서 칸이 생기기 전에 만든 프로필은 이름에만 관계가 있어, '자식들'·'부모님'이 아무도 못 찾았다 (10/07).
    """
    if member.get("relation"):
        return member["relation"]
    name = nospace(member.get("name", ""))
    for relation, words in RELATION_WORDS.items():
        if any((w in name) if len(w) >= 2 or w == "딸" else w == name for w in words):
            return relation
    return ""


def order_of(member: dict) -> str:
    if member.get("order"):
        return member["order"]
    name = nospace(member.get("name", ""))
    return next((o for o in ORDERS if o in name), "")


def _group_members(group: str, members: list[dict]) -> list[dict]:
    if group == "children":
        return [m for m in members if _is_child(m)]
    if group == "parents":
        return [m for m in members if relation_of(m) in ("아빠", "엄마")]
    if group == "grandparents":
        return [m for m in members if relation_of(m) in ("할아버지", "할머니")]
    if group == "all":
        return list(members)
    return [m for m in members if relation_of(m) == group]


# 문장에서 사람을 가리키는지 살필 일반 호칭 (프로필에 없더라도 '없는 사람'으로 잡아내 되묻기 위해)
GENERIC_PERSON_WORDS = sorted(
    {w for words in RELATION_WORDS.values() for w in words if len(w) >= 2}
    | {w for o in ORDER_WORDS.values() for words in o.values() for w in words}
    | set(CHILD_SINGULAR) | set(CHILD_PLURAL) | {w for words in GROUP_WORDS.values() for w in words}
    | {"동생", "형", "누나", "오빠", "언니", "남동생", "여동생", "손주", "손자", "손녀", "조카", "삼촌", "이모", "고모"},
    key=len, reverse=True,
)

_PARTICLES = re.compile(r"(이랑|랑|하고|와|과|이가|이는|는|은|이|가|도|의|에게|한테|꺼|거|용|들|이네|네)$")
# '들'을 남기고 조사만 뗀다 ('딸들이랑' → '딸들') — 여러 명을 가리키는지 보려고
_PARTICLES_KEEP_PLURAL = re.compile(r"(이랑|랑|하고|와|과|이가|이는|는|은|이|가|도|의|에게|한테|꺼|거|용|이네|네)$")


def nospace(text: str) -> str:
    return re.sub(r"\s+", "", text.strip())


def variants(text: str) -> list[str]:
    """비교용 형태: 띄어쓰기를 없앤 원형과, 끝 조사를 뗀 형태 ('큰애랑' → '큰애랑', '큰애')."""
    word = nospace(text)
    stripped = _PARTICLES.sub("", word)
    return [word, stripped] if stripped and stripped != word else [word]


def auto_aliases(name: str, relation: str = "", order: str = "") -> list[str]:
    """이름·관계·순서로 부르는 이름을 만든다. (사용자 별칭은 따로 더한다)"""
    words: list[str] = [name.strip()]
    words += [t for t in re.split(r"\s+", name.strip()) if t]
    words += RELATION_WORDS.get(relation, [])
    if order in ORDER_WORDS:
        words += ORDER_WORDS[order]["any"]
        if relation in ORDER_WORDS[order]:
            words += ORDER_WORDS[order][relation]
        if relation:
            words.append(f"{order} {relation}")
    seen, out = set(), []
    for w in words:
        key = nospace(w)
        if key and key not in seen:
            seen.add(key)
            out.append(w)
    return out


def member_aliases(member: dict) -> list[str]:
    return auto_aliases(member["name"], relation_of(member), order_of(member)) + list(member.get("aliases", []))


def _is_child(member: dict) -> bool:
    return relation_of(member) in CHILD_RELATIONS or bool(order_of(member))


def resolve(mentions: list[str], members: list[dict]) -> dict:
    """사람 표현들을 프로필로 연결한다.

    반환: matched [(표현, 프로필, 방식)], ambiguous [(표현, [프로필…])], unknown [표현]
    방식: 'name'(이름 그대로) / 'alias'(별칭으로)
    """
    result = {"matched": [], "ambiguous": [], "unknown": []}
    for mention in dict.fromkeys(m for m in mentions if m and m.strip()):
        keys = variants(mention)
        # 묶어 부르는 말('자식들', '부모님', '가족')과 복수형('딸들')은 해당 프로필 모두를 대상으로 한다
        word = _PARTICLES_KEEP_PLURAL.sub("", nospace(mention))
        group = next((g for g, words in GROUP_WORDS.items() if word in words or nospace(mention) in words), None)
        if group:
            group_hits = _group_members(group, members)
        elif word.endswith("들") and len(word) > 1:
            base = word[:-1]
            group_hits = [m for m in members if base in {nospace(a) for a in member_aliases(m)}]
        else:
            group_hits = []
        if group_hits:
            result["matched"].extend((mention, m, "group") for m in group_hits)
            continue
        by_name = [m for m in members if nospace(m["name"]) in keys]
        if len(by_name) == 1:
            result["matched"].append((mention, by_name[0], "name"))
            continue
        hits = [m for m in members if set(keys) & {nospace(a) for a in member_aliases(m)}]
        children = [m for m in members if _is_child(m)]
        if not hits and set(keys) & set(CHILD_SINGULAR):
            hits = children
        if len(hits) == 1:
            result["matched"].append((mention, hits[0], "alias"))
        elif len(hits) > 1:
            result["ambiguous"].append((mention, hits))
        else:
            result["unknown"].append(mention)
    return result


def mention_vocabulary(members: list[dict]) -> list[str]:
    """규칙 파서가 문장에서 사람 표현을 찾을 때 쓰는 낱말 (긴 것부터)."""
    words = {a for m in members for a in member_aliases(m) if len(nospace(a)) >= 2 or a == m["name"]}
    words |= set(GENERIC_PERSON_WORDS)
    return sorted(words, key=len, reverse=True)


def ask_message(resolved: dict, members: list[dict]) -> str | None:
    """없는 사람·애매한 사람이 있으면 되묻는 문장, 없으면 None."""
    names = ", ".join(m["name"] for m in members) or "없음"
    lines = []
    for mention, hits in resolved["ambiguous"]:
        lines.append(f"'{mention}'에 해당하는 프로필이 여러 개예요: {', '.join(h['name'] for h in hits)}. "
                     "누구인지 프로필 이름으로 말씀해 주세요.")
    for mention in resolved["unknown"]:
        lines.append(f"'{mention}'에 해당하는 가족 프로필이 없어요. 알레르기 정보를 모르는 채로 추천할 수 없어서 멈췄어요.")
    if not lines:
        return None
    lines.append(f"'가족 프로필' 탭에서 추가하거나 부르는 이름을 등록해 주세요. (등록된 프로필: {names})")
    return "\n".join(lines)


def understood_message(resolved: dict) -> str | None:
    """별칭·묶음으로 연결한 경우 "'큰애' → 첫째 아들", "'딸들' → 첫째 딸·셋째 딸"처럼 이해한 내용을 알려 준다."""
    grouped: dict[str, list[str]] = {}
    for mention, member, how in resolved["matched"]:
        if how in ("alias", "group"):
            grouped.setdefault(mention, []).append(member["name"])
    pairs = [f"'{mention}' → " + "·".join(f"'{n}'" for n in names) for mention, names in grouped.items()]
    return f"이렇게 이해했어요: {', '.join(pairs)}" if pairs else None
