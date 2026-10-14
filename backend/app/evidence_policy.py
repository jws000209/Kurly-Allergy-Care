"""Source quality and per-allergen coverage; no model decisions."""
from .allergens import ALLERGENS_19
from .allergen_context import contexts, is_truncated, valid_ingredients
from .extractor import NO_LABEL_REVIEWED


def coverage(ingredients, label, avoid):
    valid = valid_ingredients(ingredients)
    truncated = valid and is_truncated(ingredients)
    ingredient_ok = valid and not truncated  # 잘린 목록은 부재 근거로 쓰지 않는다
    declarations = [c for c in contexts(label, 'label') if c['declared']]
    declared_names = {a['name'] for a in ALLERGENS_19}
    rows = []
    for name in sorted(set(avoid)):
        sources = []
        if ingredient_ok:
            sources.append('ingredients')
        if declarations and name in declared_names:
            sources.append('label')
        if label.strip() == NO_LABEL_REVIEWED and name in declared_names:
            sources.append('raw-reviewed')
        rows.append({'name': name, 'sufficient': bool(sources), 'sources': sources})
    return {'ingredients_quality': 'usable' if ingredient_ok else 'truncated' if truncated else 'missing' if not ingredients.strip() else 'damaged',
            'label_declarations': declarations, 'allergens': rows,
            'missing': [r['name'] for r in rows if not r['sufficient']]}
