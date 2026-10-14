"""Shared source-preserving classification of allergen evidence clauses."""
import re

MISSING = re.compile(r"^(?:(?:원재료(?:명|\s*정보)?|ingredients?)\s*[:：]?\s*)?(?:[-—?]+|n/?a|none|null|unknown|정보\s*없음|없음|미상|미제공|확인\s*불가|상세\s*페이지\s*(?:참조|참고)|사진\s*참조)$", re.I)
INSTRUCTIONS = re.compile(r"직사광선|보관하십시오|소비기한|유통기한|storage instructions|store in|keep refrigerated", re.I)
CROSS = re.compile(r"혼입|교차|제조\s*(?:시설|라인|공간)|(같은|동일|동일한)\s*(시설|공간|라인|공장)|사용(?:한|하는)\s*제품과|may\s+contain|(?:facility|equipment|line).*(?:process|handl|manufactur)|(?:process|manufactur).*facility", re.I)
NEGATIVE = re.compile(r"함유\s*(?:없|아님|하지\s*않)|함유하지\s*않|불포함|미포함|무첨가|프리(?!미엄)|없음|\bwithout\b|\bno\s+(?:added\s+)?|\bfree\s+of\b|[-\s]free\b|\b(?:is|does)\s+not\s+contain(?:ed)?\b", re.I)
FLAVOR = re.compile(r"(?<=[가-힣])맛(?:$|\s)|향료|(?<=[가-힣])향(?:$|\s)|\bflavou?r(?:ing|ed)?\b", re.I)
DECLARED = re.compile(r"함유|포함|\bcontains?\b", re.I)
BOUNDARY = re.compile(r"\n|;|\.\s|※|\*|(?:^|\s)-\s*|(?=(?:이|본|해당)\s*제품은)|(?<=함유)(?!\s*(?:없|아니|아님|하지))|(?<=있음)|(?<=혼입가능)|(?<=혼입\s가능)(?!성)|(?<=가능성)")


# 한글 사이 숫자는 표준 표기('향료2종', '제1호', '1일1포')일 때만 정상이다. 그 밖의 '여0들', '지|소'는 OCR 손상.
DIGIT_JOIN = re.compile(r"[가-힣][0-9|][가-힣]")
UNIT_JOIN = re.compile(r"[가-힣][0-9][종호길일포회분조번개차]")


def _damaged_digit_join(text):
    return any(not UNIT_JOIN.fullmatch(m.group()) for m in DIGIT_JOIN.finditer(text))


def is_truncated(text):
    """괄호가 짝이 맞지 않으면 목록이 잘렸을 수 있다. 있는 성분의 긍정 검출에는 쓰고, 없다는 판단에는 쓰지 않는다."""
    text = (text or "").strip()
    return text.count("(") != text.count(")")


def valid_ingredients(text):
    text = (text or "").strip()
    if re.search(r"취급\s*시", text) or _damaged_digit_join(text) or text.count("{") != text.count("}"):
        return False
    return bool(text) and not any(MISSING.fullmatch(t.strip()) for t in re.split(r"[,;\n]", text) if t.strip()) and not INSTRUCTIONS.search(text)


def contexts(text, source):
    text = text or ""
    if source == "label":
        trailing = re.search(r"소비기한\s*\(|상품선택|안내사항", text)
        if trailing:
            text = text[:trailing.start()]
    result = []
    start = 0
    boundaries = list(BOUNDARY.finditer(text))
    for boundary in boundaries + [None]:
        end = boundary.start() if boundary else len(text)
        clause = text[start:end]
        cross = bool(CROSS.search(clause))
        # Commas in a cross-contact clause share the clause's qualifier.
        pieces = [re.match(r"[\s\S]*", clause)] if cross else list(re.finditer(r"[^,]+", clause))
        for piece in pieces:
            raw = piece.group()
            if not raw.strip():
                continue
            offset = start + piece.start()
            kind = "cross" if cross else "negative" if NEGATIVE.search(raw) else "flavor" if FLAVOR.search(raw) else "contains"
            result.append({"quote": raw, "start": offset, "end": offset + len(raw), "source": source,
                           "kind": kind, "declared": bool(DECLARED.search(raw)) and kind == "contains"})
        start = boundary.end() if boundary else len(text)
    return result
