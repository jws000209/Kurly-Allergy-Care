"""공통 판정 경계: 기본 XLSX+GPT RAG, rule 모드는 기존 규칙 비교용.

확정 차단 근거는 GPT가 해제할 수 없다.
"""

from .allergens import ALLERGEN_NAMES, ALLERGENS_19
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


def _judge_rules(product: dict, avoid: list[str], strict: bool = False) -> dict:
    """strict=True면 '혼입 가능' 표시도 BLOCK으로 본다."""
    avoid_set = set(avoid)
    matched = sorted(avoid_set & set(product.get("contains", [])))
    cross_matched = sorted(avoid_set & set(product.get("cross", [])))

    if matched:  # 함유 검출은 근거가 부족해도 차단 (안전 쪽)
        return {"status": BLOCK, "matched": matched, "cross_matched": cross_matched,
                "reason": f"{', '.join(matched)} 함유"}
    if cross_matched and strict:
        return {"status": BLOCK, "matched": [], "cross_matched": cross_matched,
                "reason": f"{', '.join(cross_matched)} 혼입 가능 (엄격 모드)"}
    if not product.get("verified"):
        reason = ("함유 성분 표시 없이 '같은 제조시설' 문구만 있어 판정할 수 없습니다. 상품 뒷면 표시 확인 필요."
                  if product.get("cross") else "원재료명과 알레르기 함유 표시가 없어 판정할 수 없습니다. 정보 확인 필요.")
        return {"status": UNVERIFIED, "matched": [], "cross_matched": cross_matched, "reason": reason}
    if product.get("basis") == "공식표시":
        # 공식 표시는 법정 19종만 적는다. 확장 20종은 표시가 없어도 함유를 확인할 수 없다.
        declared = {a["name"] for a in ALLERGENS_19}
        not_declared = sorted(a for a in avoid_set if a in ALLERGEN_NAMES and a not in declared)
        if not_declared:
            return {"status": UNVERIFIED, "matched": [], "cross_matched": cross_matched,
                    "reason": f"{', '.join(not_declared)}은(는) 공식 표시 대상이 아니라 확인할 수 없습니다. 원재료 확인 필요."}
    if cross_matched and strict:
        return {"status": BLOCK, "matched": [], "cross_matched": cross_matched,
                "reason": f"{', '.join(cross_matched)} 혼입 가능 (엄격 모드)"}
    reason = "회피 알레르겐 미검출"
    if product.get("allergy_label", "").strip() == NO_LABEL_REVIEWED:
        reason += " · 알레르기 표시가 없어 원물·단순 구성 기준으로 판단 (라벨 확인 아님)"
    if cross_matched:
        reason += f" · 주의: {', '.join(cross_matched)} 혼입 가능 표시"
    return {"status": PASS, "matched": [], "cross_matched": cross_matched, "reason": reason}


def judge(product: dict, avoid: list[str], strict: bool = False, *, generate: bool = True) -> dict:
    """Shared decision boundary. RAG cannot override established BLOCK evidence."""
    import os
    if os.getenv("KAC_ALLERGY_PROVIDER", "rule") == "rule":
        return _judge_rules(product, avoid, strict)
    from .allergen_rag import assess
    return assess(product, avoid, strict, generate=generate)
