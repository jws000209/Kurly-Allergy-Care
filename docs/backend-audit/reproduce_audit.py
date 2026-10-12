import argparse
import itertools
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import tempfile

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / 'backend'
OUT = Path.cwd() / 'backend-audit-results'
OUT.mkdir(parents=True, exist_ok=True)
WORK = Path(tempfile.mkdtemp(prefix='backend-audit-', dir=OUT))
sys.path.insert(0, str(BACKEND))
for key in ('LLM_MODEL', 'GOOGLE_API_KEY', 'GROQ_API_KEY', 'ANTHROPIC_API_KEY', 'OPENAI_API_KEY'):
    os.environ[key] = ''
os.environ['LANGSMITH_TRACING'] = 'false'
os.environ['KAC_DB_PATH'] = str(WORK / 'probe.sqlite3')
os.environ['KAC_SEED_PATH'] = str(WORK / 'empty.json')
os.environ['KAC_LIVE_MIN_EA_PATH'] = str(WORK / 'min-ea.json')

import httpx
from app import cart_builder, recommender
from app.allergens import ALLERGEN_NAMES
from app.extractor import NO_LABEL_REVIEWED, extract
from app.rule_engine import judge


results = {'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPO, text=True).strip(),
           'python': sys.version, 'unit_probes': [], 'seed_analysis': {}, 'servers': []}


def probe(name, expected, actual, details=None):
    results['unit_probes'].append({'name': name, 'passed': actual == expected,
                                  'expected': expected, 'actual': actual, 'details': details})


def algorithm_probes():
    coverage = {name: name in extract(name, '')['contains'] for name in ALLERGEN_NAMES}
    probe('39 canonical allergens detected', 39, sum(coverage.values()), coverage)
    cross = extract('정제수, 설탕', '우유를 사용한 제품과 같은 제조시설')
    probe('strict cross contamination blocked', 'BLOCK', judge(cross, ['우유'], True)['status'])
    probe('non-strict cross contamination warning', 'PASS', judge(cross, ['우유'], False)['status'])
    probe('missing evidence excluded', 'UNVERIFIED', judge(extract('', ''), ['우유'])['status'])
    matrix = {1: {'a': 5}, 2: {'a': 5, 'b': 5, 'c': 1}, 3: {'x': 5, 'c': 5}}
    probe('item weighted average single-anchor tie', {'b': 1.0, 'c': 1.0}, recommender.item_scores(matrix[1], matrix))
    rows = [{'user_id': uid, 'product_id': pid, 'event': ev} for uid, pid, ev in
            [(1, 'a', 'purchase'), (2, 'a', 'purchase'), (2, 'b', 'purchase'),
             (2, 'c', 'cart'), (3, 'a', 'purchase'), (3, 'b', 'purchase'), (3, 'd', 'purchase')]]
    probe('co-purchase buyer fractions', {'b': 1.0, 'd': 0.5}, recommender.co_purchase_scores({'a'}, rows, 1))
    pool = [{'id': str(i), 'name': str(p), 'price': p, 'category': 'same', 'score': 3}
            for i, p in enumerate([1000, 3000, 5000, 10000])]
    budget = {'amount': 8000, 'mode': 'around'}
    picked, notes = cart_builder.build(pool, 2, budget)
    valid = [[p['price'] for p in combo] for combo in itertools.combinations(pool, 2)
             if 6800 <= sum(p['price'] for p in combo) <= 9200]
    probe('around budget finds feasible 2-item basket', 2, len(picked),
          {'pool_prices': [p['price'] for p in pool], 'budget': budget, 'feasible': valid,
           'actual_prices': [p['price'] for p in picked], 'notes': notes})
    inputs = [('캐슈밀크', '우유'), ('캐슈버터', '우유'), ('루핀콩', '대두'), ('바나나밀', '밀')]
    for ingredient, false_allergen in inputs:
        actual = extract(ingredient, '')['contains']
        probe(f'plant ingredient false positive: {ingredient}', False, false_allergen in actual, actual)
    product = {**extract('', NO_LABEL_REVIEWED), 'allergy_label': NO_LABEL_REVIEWED}
    probe('19-only reviewed evidence does not verify expanded apple', 'UNVERIFIED', judge(product, ['사과'])['status'])


def seed_analysis():
    for label, filename in [('default', 'kurly_products.json'), ('material', 'kurly_products_material.json')]:
        seed = json.loads((BACKEND / 'data' / filename).read_text(encoding='utf-8'))
        reviewed = [p for p in seed if p.get('allergy_label') == NO_LABEL_REVIEWED]
        apple = [p for p in reviewed if '사과' in p['name']]
        results['seed_analysis'][label] = {
            'file': filename, 'products': len(seed), 'ingredients_present': sum(bool(p.get('ingredients')) for p in seed),
            'reviewed_19_only': len(reviewed),
            'unverified': sum(not extract(p.get('ingredients'), p.get('allergy_label'))['verified'] for p in seed),
            'apple_examples': [{'id': p['id'], 'name': p['name'], 'ingredients': p.get('ingredients'),
                                'status_avoiding_apple': judge({**p, **extract(p.get('ingredients'), p.get('allergy_label'))}, ['사과'])['status']}
                               for p in apple[:5]],
            'nonpositive_prices': sum(not p.get('price') or p['price'] <= 0 for p in seed),
        }


def http_server(label, seed_path, synthetic=False, asgi=False):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    env = os.environ.copy()
    env.update(KAC_DB_PATH=str(WORK / f'http-{label}.sqlite3'), KAC_SEED_PATH=str(seed_path),
               KAC_LIVE_MIN_EA_PATH=str(WORK / f'http-{label}-min.json'), PYTHONUTF8='1')
    log_path = WORK / f'{"asgi" if asgi else "uvicorn"}-{label}.log'
    server = {'label': label, 'seed': str(seed_path), 'port': port if not asgi else None,
              'transport': 'in-process ASGI' if asgi else 'TCP HTTP', 'requests': []}
    results['servers'].append(server)
    with log_path.open('w', encoding='utf-8') as log:
        proc = None if asgi else subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', str(port)],
                                cwd=BACKEND, env=env, stdout=log, stderr=subprocess.STDOUT,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        start = time.perf_counter()
        try:
            if proc is not None:
                for _ in range(1200):
                    if proc.poll() is not None:
                        raise RuntimeError(log_path.read_text(encoding='utf-8'))
                    if 'Uvicorn running on' in log_path.read_text(encoding='utf-8'):
                        break
                    time.sleep(0.1)
                else:
                    raise TimeoutError('server did not become ready')
            if asgi:
                from fastapi.testclient import TestClient
                from app import db
                from app.main import app
                db.DB_PATH = Path(env['KAC_DB_PATH'])
                db.SEED_PATH = seed_path
                db.LIVE_MIN_EA_PATH = Path(env['KAC_LIVE_MIN_EA_PATH'])
            connection = TestClient(app) if asgi else httpx.Client(base_url=f'http://127.0.0.1:{port}', timeout=5, trust_env=False)
            with connection as client:
                for attempt in range(240):
                    if proc is not None and proc.poll() is not None:
                        raise RuntimeError(log_path.read_text(encoding='utf-8'))
                    try:
                        health = client.get('/api/health')
                        if health.status_code == 200:
                            break
                    except httpx.ConnectError:
                        pass
                    time.sleep(0.25)
                else:
                    raise TimeoutError('server startup timed out')
                server['startup_seconds'] = round(time.perf_counter() - start, 3)
                server['health'] = health.json()

                def call(method, path, payload=None, detail=False):
                    tick = time.perf_counter()
                    r = client.request(method, path, json=payload) if payload is not None else client.request(method, path)
                    record = {'method': method, 'path': path, 'status': r.status_code,
                              'milliseconds': round((time.perf_counter() - tick) * 1000, 2)}
                    data = r.json() if 'json' in r.headers.get('content-type', '') else None
                    if detail:
                        record['response'] = data
                    server['requests'].append(record)
                    return r, data

                call('GET', '/docs')
                call('GET', '/openapi.json')
                _, allergens = call('GET', '/api/allergens')
                server['allergen_count'] = len(allergens)
                _, user = call('POST', '/api/login', {'name': f'audit-{label}'})
                _, member = call('POST', f"/api/users/{user['id']}/members", {'name': '본인', 'allergens': ['우유'], 'strict': True})
                _, products = call('GET', '/api/products')
                anchor = next(p['id'] for p in products if '주스' in p['name'])
                # Construct known purchase histories in isolated test DB, never in user data.
                _, other = call('POST', '/api/login', {'name': f'audit-peer-{label}'})
                candidates = [p for p in products if judge(p, ['우유'], True)['status'] == 'PASS' and p['id'] != anchor]
                alternative = next(p['id'] for p in candidates if '주스' in p['name'])
                for uid, pid in [(user['id'], anchor), (other['id'], anchor), (other['id'], alternative)]:
                    call('POST', f'/api/users/{uid}/interactions', {'product_id': pid, 'event': 'purchase'})
                for mode in ['item', 'user', 'content', 'hybrid']:
                    _, data = call('POST', '/api/recommendations', {'user_id': user['id'], 'member_ids': [member['id']],
                                         'mode': mode, 'count': 5, 'budget': 20000, 'keywords': ['주스']})
                    checks = {'mode': mode, 'count': len(data['items']), 'total': sum(p['price'] for p in data['items']),
                              'methods': sorted({p.get('recommendation_method') for p in data['items']}),
                              'all_pass': all(p['status'] == 'PASS' for p in data['items']),
                              'known_blocked_or_unverified_returned': any(judge(next(q for q in products if q['id'] == p['id']), ['우유'], True)['status'] != 'PASS' for p in data['items']),
                              'stats': data['stats'], 'items': [{'id': p['id'], 'name': p['name'], 'score': p.get('recommendation_score')} for p in data['items']]}
                    server.setdefault('recommendations', []).append(checks)
                for message in ['주스 2개 2만원 이내', '1번 상품 빼줘', '주스 1개 추가해줘', '더 저렴하게']:
                    _, data = call('POST', '/api/chat', {'user_id': user['id'], 'thread_id': label, 'message': message,
                                                       'selected_member_ids': [member['id']]})
                    server.setdefault('chat', []).append({'message': message, 'parser': data['parser'], 'action': data['intent']['action'],
                                                        'asked': data['asked'], 'count': len(data['cart']),
                                                        'total': sum(p['price'] for p in data['cart']), 'reply': data['reply']})
                _, expanded = call('POST', f"/api/users/{user['id']}/members", {'name': '사과회피', 'allergens': ['사과']})
                _, apple = call('POST', '/api/recommendations', {'user_id': user['id'], 'member_ids': [expanded['id']],
                                                                'keywords': ['사과'], 'count': 5, 'exclude_seen': False})
                server['apple_avoid_recommendations'] = [{'id': p['id'], 'name': p['name'], 'status': p['status'], 'reason': p['reason']} for p in apple['items']]
                for ingredient, label_text, expected in [('참깨, 설탕', '', 'BLOCK'), ('정제수', '우유를 사용한 제품과 같은 제조시설', 'BLOCK'), ('', '', 'UNVERIFIED')]:
                    mid = expanded['id'] if '참깨' not in ingredient else None
                    if mid is None:
                        _, sesame = call('POST', f"/api/users/{user['id']}/members", {'name': '참깨회피', 'allergens': ['참깨']})
                        mid = sesame['id']
                    if '제조시설' in label_text:
                        mid = member['id']
                    _, data = call('POST', '/api/check', {'user_id': user['id'], 'member_ids': [mid], 'save': False,
                                          'product': {'id': 'audit-only', 'name': '검증상품', 'ingredients': ingredient, 'allergy_label': label_text}})
                    server.setdefault('check_cases', []).append({'ingredients': ingredient, 'label': label_text, 'expected': expected, 'actual': data['status']})
                # Unknown profile must not silently erase avoidance constraints.
                _, bad = call('POST', '/api/check', {'user_id': user['id'], 'member_ids': [999999], 'save': False,
                                  'product': {'id': 'bad-profile', 'name': '우유', 'ingredients': '우유'}})
                server['invalid_profile_check'] = bad
                _, _, = call('POST', '/api/recommendations', {'user_id': user['id'], 'member_ids': [999999]}, True)
                if synthetic:
                    _, data = call('POST', '/api/chat', {'user_id': user['id'], 'thread_id': 'budget-around',
                                                        'message': '주스 2개 8천원 안팎', 'selected_member_ids': [member['id']]}, True)
                    server['around_budget_chat'] = data
                    # Explicit group counts must respect requested total budget.
                    _, data = call('POST', '/api/chat', {'user_id': user['id'], 'thread_id': 'groups',
                                                        'message': '주스 3개랑 콜라 1개 1만원 이내', 'selected_member_ids': [member['id']]}, True)
                    server['group_chat'] = data
        except Exception as exc:
            server['error'] = f'{type(exc).__name__}: {exc}'
        finally:
            if proc is not None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=10)
            server['stopped'] = proc is None or proc.poll() is not None


def main():
    args = argparse.ArgumentParser()
    args.add_argument('--asgi', action='store_true')
    options = args.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    algorithm_probes()
    seed_analysis()
    for label, filename in [('default', 'kurly_products.json'), ('material', 'kurly_products_material.json')]:
        http_server(label, BACKEND / 'data' / filename, asgi=options.asgi)
    fixture = []
    for i, price in enumerate([1000, 3000, 5000, 10000]):
        fixture.append({'id': f'j{i}', 'name': f'주스 {i}', 'price': price, 'category': '음료>주스',
                        'ingredients': '정제수, 설탕', 'allergy_label': '해당 없음', 'source': 'kurly', 'review_count': 100 - i})
    fixture.append({'id': 'cola', 'name': '콜라', 'price': 1000, 'category': '음료>탄산음료', 'ingredients': '정제수, 설탕', 'source': 'kurly'})
    fixture_path = WORK / 'synthetic.json'
    fixture_path.write_text(json.dumps(fixture, ensure_ascii=False), encoding='utf-8')
    http_server('synthetic', fixture_path, True, options.asgi)
    result_name = 'audit-asgi-results.json' if options.asgi else 'audit-results.json'
    (OUT / result_name).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'failed_probes': [p for p in results['unit_probes'] if not p['passed']],
                      'seeds': results['seed_analysis'], 'servers': [{k: v for k, v in s.items() if k in ('label', 'health', 'startup_seconds', 'error', 'stopped', 'apple_avoid_recommendations')} for s in results['servers']]}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
