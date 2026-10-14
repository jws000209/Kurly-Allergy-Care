"""Public explanations contain product facts, never model or pipeline diagnostics."""


def verdict_reason(verdict):
    if verdict['status'] == 'PASS':
        return '확인된 정보에서 회피 성분이 검출되지 않았어요. 제품 포장 표시도 확인해 주세요.'
    matched = verdict.get('matched') or verdict.get('cross_matched') or []
    if verdict['status'] == 'BLOCK' and matched:
        kind = '혼입 가능' if verdict.get('cross_matched') and not verdict.get('matched') else '함유'
        return f"{', '.join(matched)} {kind} 표시가 있어 추천에서 제외했어요."
    return '성분 정보를 충분히 확인할 수 없어 추천에서 제외했어요.'
