"""Conservative user scope resolution; unknown or broad requests must be clarified."""
import re
from .allergens import ALLERGENS, ALLERGEN_NAMES

GROUPS = {
    "갑각류": ["게", "새우"], "crustaceans": ["게", "새우"], "crustacean": ["게", "새우"],
}
BROAD = {"견과류", "견과", "어류", "생선", "해산물", "연체류", "nuts", "tree nuts", "fish", "shellfish", "seafood", "콩류"}
EXTRA = {"대하": "새우", "shrimp": "새우", "prawn": "새우", "crab": "게", "milk": "우유", "egg": "알류", "eggs": "알류", "soy": "대두", "wheat": "밀"}
ALIASES = {alias: [a["name"]] for a in ALLERGENS for alias in a["aliases"]}
ALIASES.update({key: [value] for key, value in EXTRA.items()})
ALIASES.update(GROUPS)
CUE = re.compile(r"알레르기|알러지|allerg|피하|피하고|제외|빼|없이|없는|프리|못\s*먹", re.I)
NEGATIVE = re.compile(r"괜찮|알(?:레르기|러지)(?:는|가)?\s*없|not\s+allergic", re.I)
SUFFIX = r"(?:만|은|는|이|가|을|를|랑|와|과|이랑|도|없이|없는|알러지|알레르기)*"
PATTERN = re.compile(r"(?<![가-힣a-z])(" + "|".join(re.escape(k) for k in sorted(set(ALIASES) | BROAD, key=len, reverse=True)) + r")" + SUFFIX + r"(?![가-힣a-z])", re.I)


def resolve_term(term):
    key = term.strip().casefold()
    return ALIASES.get(key, []), key in BROAD


def mentions(text):
    return [(m.group(1), m.start(), m.end()) for m in PATTERN.finditer(text)]


def scan(message, *, confirmation=False):
    avoid, original, unresolved, allowed = [], [], [], []
    for clause in re.split(r"(?<=괜찮고)|(?<=없고)|(?<=없지만)|(?<=괜찮지만)|하지만|그런데|\bbut\b|[.!?\n]", message, flags=re.I):
        found = mentions(clause)
        allergy_word = re.search(r"알러지|알레르기", clause)
        if allergy_word and not NEGATIVE.search(clause):
            prefix = clause[:allergy_word.start()].strip()
            for part in re.split(r"[·,/] |[·,/]|(?:랑|와|과|하고)\s+", prefix):
                tokens = part.strip().split()
                if tokens:
                    token = tokens[-1]
                    if not mentions(token):
                        unresolved.append(token)
        if not found:
            if CUE.search(clause):
                # Do not silently discard an unsupported ingredient immediately before allergy wording.
                unknown = re.search(r"([가-힣A-Za-z]+?)(?:은|는|만|이|가)?\s*(?:알러지|알레르기)", clause)
                if unknown:
                    unresolved.append(unknown.group(1))
                english = re.search(r"allergic\s+to\s+([a-z][a-z\s-]*?)(?:[,.;!?]|$)", clause, re.I)
                if english and not NEGATIVE.search(clause):
                    unresolved.append(english.group(1).strip())
            continue
        if NEGATIVE.search(clause):
            if len(found) > 1:
                unresolved.append("여러 성분의 긍정·부정 범위를 확인해야 함")
            if any(word.casefold() in GROUPS for word, _, _ in found) and len(found) > 1:
                unresolved.append("상위 분류와 예외가 함께 제시됨")
            allowed.extend(name for word, _, _ in found for name in ALIASES.get(word.casefold(), []))
            continue
        if not (confirmation or CUE.search(clause)):
            continue
        for word, _, _ in found:
            original.append(word)
            names, broad = resolve_term(word)
            if broad:
                unresolved.append(word)
            avoid.extend(names)
    if any(word.casefold() in GROUPS for word in original) and allowed:
        unresolved.append("상위 분류와 예외가 함께 제시됨")
    return {"avoid": list(dict.fromkeys(avoid)), "original_mentions": list(dict.fromkeys(original)),
            "unresolved_mentions": list(dict.fromkeys(unresolved)), "explicitly_allowed": list(dict.fromkeys(allowed))}


def enforce(intent, message, *, confirmation=False):
    scope = scan(message, confirmation=confirmation)
    names, unknown = [], list(scope["unresolved_mentions"])
    for term in intent.get("excluded_allergens", []):
        resolved, broad = resolve_term(term)
        if not resolved or broad:
            unknown.append(term)
        names.extend(resolved)
    # A negative statement cannot remove a saved profile restriction; only suppress
    # contradictory extra conditions inferred from this sentence.
    names = [n for n in names if n not in scope["explicitly_allowed"]]
    intent["excluded_allergens"] = list(dict.fromkeys(names + scope["avoid"]))
    intent.update(original_mentions=scope["original_mentions"], unresolved_mentions=list(dict.fromkeys(unknown)))
    intent["needs_allergen_confirmation"] = bool(intent["unresolved_mentions"])
    return intent
