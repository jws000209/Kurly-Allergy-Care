from app import agent, cart_builder
from app.allergen_context import is_truncated, valid_ingredients
from app.allergen_dictionary import get_dictionary


def test_ocr_garbage_and_truncated_parentheses():
    assert not valid_ingredients("여0들서부 취급 시")
    assert not valid_ingredients("지|소, 설탕")
    assert valid_ingredients("향료2종, 제1호 색소, 1일1포")          # 표준 단위 표기는 손상이 아니다
    assert valid_ingredients("소맥분(밀:호주산, 설탕") and is_truncated("소맥분(밀:호주산, 설탕")   # 잘림: 긍정 검출만
    assert valid_ingredients("소맥분(밀), 설탕") and not is_truncated("소맥분(밀), 설탕")


def test_miso_does_not_match_inside_unrelated_ingredients():
    d = get_dictionary()
    assert not any(h['name'] == '대두' for h in d.retrieve('풍미조미소스, 볶음조미소맥분'))
    assert any(h['name'] == '대두' for h in d.retrieve('미소'))


def test_replenishes_only_verified_candidates_and_keeps_internal_reasons(monkeypatch):
    products = {i: {'id': i, 'name': i, 'price': 100} for i in ['old', 'bad', 'good']}
    monkeypatch.setattr(cart_builder.db, 'get_product', products.get)
    monkeypatch.setattr(cart_builder, 'judge', lambda p, *args: {'status': 'PASS' if p['id'] == 'good' else 'UNVERIFIED', 'reason': 'GPT internal', 'rag': {'assessment': {'reason': 'OCR internal'}}})
    monkeypatch.setattr(cart_builder, 'to_item', lambda p, v: p)
    monkeypatch.setattr(cart_builder, 'search', lambda *args, **kwargs: ([products['bad'], products['good']], {}))
    state = {'constraints': {'avoid': ['새우'], 'strict': False, 'terms': ['라면'], 'budget': 500, 'profiles': ['아들', '아들2']}, 'intent': {'action': 'create_cart'}, 'cart': [products['old']], 'user_id': 1}
    result = agent.revalidate(state)
    assert [p['id'] for p in result['cart']] == ['good']
    assert len(result['validation_diagnostics']) == 3
    assert result['validation_diagnostics'][0]['verdict']['reason'] == 'GPT internal'
    reply = agent.respond({**state, **result})['reply']
    assert 'GPT' not in reply and '재검증' not in reply
    empty = agent.respond({**state, 'cart': []})['reply']
    assert '공통 추천 목록' not in empty and '찾지 못했어요' in empty


def test_partial_page_check_keeps_known_declaration():
    from fastapi.testclient import TestClient
    from app.main import app
    from app import db
    with TestClient(app) as client:
        user = client.post('/api/login', json={'name': 'partial-page-test'}).json()
        member = client.post(f"/api/users/{user['id']}/members", json={'name': 'tester', 'allergens': ['새우']}).json()
        db.upsert_product({'id': 'partial', 'name': '라면', 'ingredients': '쌀', 'allergy_label': '새우 함유'})
        response = client.post('/api/check', json={'user_id': user['id'], 'member_ids': [member['id']], 'product': {'id': 'partial', 'name': '라면', 'ingredients': '', 'allergy_label': '밀 함유'}})
        assert response.status_code == 200
        assert response.json()['status'] == 'BLOCK'
        saved = db.get_product('partial')
        assert '새우 함유' in saved['allergy_label'] and saved['ingredients'] == '쌀'


def test_final_empty_cart_clears_stale_count_notes(monkeypatch):
    monkeypatch.setattr(cart_builder, 'revalidate', lambda *a, **kw: ([], []))
    result = agent.revalidate({'constraints': {'avoid': ['새우'], 'strict': True, 'budget': None},
                              'cart': [{'id': 'x'}], 'notes_after': ['추천 상품 5개'], 'intent': {'action': 'remove_item'}})
    assert result['cart'] == [] and result['notes_after'] == []
