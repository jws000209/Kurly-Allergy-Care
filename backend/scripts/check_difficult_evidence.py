"""Offline policy experiment on problematic catalog records; never modifies source/DB."""
import json
from collections import Counter
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.allergen_context import valid_ingredients
from app.allergen_rag import assess
from app.extractor import extract

ROOT = Path(__file__).resolve().parents[2]
AVOID = ['땅콩', '메밀', '복숭아', '새우', '오징어']
IDS = {'1001642002', '1002131434', '1001642093', '5061314', '1002178667'}


def compare(product, strict):
    current = assess(product, AVOID, strict, generate=False)
    original = extract(product.get('ingredients', ''), product.get('allergy_label', ''))
    fallback = dict(product)
    # Experiment: discard unreliable OCR as absence evidence, preserve its positive detections.
    if not valid_ingredients(product.get('ingredients')):
        fallback['ingredients'] = ''
        fallback['contains'] = sorted(set(product.get('contains', [])) | set(original['contains']))
        fallback['cross'] = sorted(set(product.get('cross', [])) | set(original['cross']))
    proposed = assess(fallback, AVOID, strict, generate=False)
    return current, proposed


def main():
    products = json.loads((ROOT / 'backend/data/kurly_products_material.json').read_text(encoding='utf-8'))
    difficult = [p for p in products if not valid_ingredients(p.get('ingredients'))]
    report = {'scope': 'offline candidate gates, not GPT final approval', 'avoid': AVOID,
              'catalog_count': len(products), 'difficult_count': len(difficult), 'modes': {}, 'original_five': []}
    for strict in (False, True):
        rows = [(p, *compare(p, strict)) for p in difficult]
        ramen = [r for r in rows if '라면' in r[0].get('category', '')]
        report['modes'][str(strict)] = {
            'current': dict(Counter(c['status'] for _, c, _ in rows)),
            'proposed': dict(Counter(n['status'] for _, _, n in rows)),
            'difficult_ramen_count': len(ramen),
            'ramen_current': dict(Counter(c['status'] for _, c, _ in ramen)),
            'ramen_proposed': dict(Counter(n['status'] for _, _, n in ramen)),
            'surviving_ramen': [{'id': p['id'], 'name': p['name'], 'reason': n['reason']} for p, _, n in ramen if n['status'] == 'PASS'][:8],
        }
    for p in products:
        if str(p['id']) in IDS:
            c, n = compare(p, False)
            report['original_five'].append({'id': p['id'], 'name': p['name'], 'current': c['status'], 'proposed': n['status'], 'reason': n['reason']})
    # User-supplied declaration is a hypothetical input, not a catalog correction.
    p = dict(next(p for p in products if str(p['id']) == '1002178667'))
    p['allergy_label'] = '밀, 대두, 돼지고기, 우유, 계란, 새우, 쇠고기 함유\n' + p['allergy_label']
    report['user_declaration_hypothesis'] = {str(s): compare(p, s)[1]['status'] for s in (False, True)}
    destination = ROOT / 'docs/difficult-evidence-experiment.json'
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
