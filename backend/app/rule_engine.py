"""Rule Engine: 프로필 알레르겐과 상품 추출 알레르겐을 결정론적으로 대조한다.

알레르기 판정은 여기서만 한다. LLM은 이 모듈의 결과를 바꾸지 못한다.
"""

from .extractor import NO_LABEL_REVIEWED

PASS = "PASS"
BLOCK = "BLOCK"
UNVERIFIED = "UNVERIFIED"


def combine_profiles(members: list[dict], extra_excluded: list[str] | None = None) -> dict:
    """여러 구성원을 고르면 알레르겐 합집합을 회피 조건으로 쓴다."""
    avoid: set[str] = set(extra_excluded or [])
    for member in members:
        avoid.update(member["allergens"])
    return {
        "avoid": sorted(avoid),
        "strict": any(member.get("strict") for member in members),
        "profiles": [member["name"] for member in members],
    }


def judge(product: dict, avoid: list[str], strict: bool = False) -> dict:
    """strict=True면 '혼입 가능' 표시도 BLOCK으로 본다."""
    if not product.get("verified"):
        reason = ("함유 성분 표시 없이 '같은 제조시설' 문구만 있어 판정할 수 없습니다. 상품 뒷면 표시 확인 필요."
                  if product.get("cross") else "원재료·알레르기 정보가 없어 판정할 수 없습니다. 정보 확인 필요.")
        return {"status": UNVERIFIED, "matched": [], "cross_matched": [], "reason": reason}

    avoid_set = set(avoid)
    matched = sorted(avoid_set & set(product.get("contains", [])))
    cross_matched = sorted(avoid_set & set(product.get("cross", [])))

    if matched:
        return {"status": BLOCK, "matched": matched, "cross_matched": cross_matched,
                "reason": f"{', '.join(matched)} 함유"}
    if cross_matched and strict:
        return {"status": BLOCK, "matched": [], "cross_matched": cross_matched,
                "reason": f"{', '.join(cross_matched)} 혼입 가능 (엄격 모드)"}
    reason = "회피 알레르겐 미검출"
    if product.get("allergy_label", "").strip() == NO_LABEL_REVIEWED:
        reason += " · 알레르기 표시가 없어 원물·단순 구성 기준으로 판단 (라벨 확인 아님)"
    if cross_matched:
        reason += f" · 주의: {', '.join(cross_matched)} 혼입 가능 표시"
    return {"status": PASS, "matched": [], "cross_matched": cross_matched, "reason": reason}
