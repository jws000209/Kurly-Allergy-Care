import pytest
from app import allergen_rag as rag, intent


@pytest.fixture(autouse=True)
def provider(monkeypatch):
    monkeypatch.setenv('KAC_ALLERGY_PROVIDER', 'rag')


def test_damaged_ocr_can_use_label_for_declared_scope():
    result = rag.assess({'ingredients': '여0들서부 취급 시', 'allergy_info': '밀, 대두 함유'}, ['새우'], generate=False)
    assert result['status'] == 'PASS'
    coverage = result['rag']['source_coverage']
    assert coverage['ingredients_quality'] == 'damaged'
    assert coverage['allergens'][0]['sources'] == ['label']
    assert result['rag']['source_texts']['allergy_info'] == '밀, 대두 함유'


def test_expanded_scope_is_not_cleared_by_official_label():
    result = rag.assess({'ingredients': '쌀(중국산', 'allergy_label': '밀 함유'}, ['사과'], generate=False)
    assert result['status'] == 'UNVERIFIED'
    assert result['rag']['source_coverage']['missing'] == ['사과']


def test_both_label_fields_and_positive_ocr_are_preserved():
    p = {'ingredients': '우유, 쌀(', 'allergy_label': '밀 함유', 'allergy_info': '새우 함유'}
    assert rag.assess(p, ['새우'], generate=False)['status'] == 'BLOCK'
    assert rag.assess(p, ['우유'], generate=False)['status'] == 'BLOCK'


def test_cross_only_cannot_prove_absence():
    p = {'ingredients': '취급 시', 'allergy_info': '새우를 사용하는 제품과 같은 제조시설'}
    assert rag.assess(p, ['새우'], False, generate=False)['status'] == 'UNVERIFIED'
    assert rag.assess(p, ['새우'], True, generate=False)['status'] == 'BLOCK'


def test_model_status_is_advisory_and_unresolved_is_fail_closed(monkeypatch):
    import json
    monkeypatch.setattr(intent, 'llm_models', lambda: ['mock'])
    def generate(version, model, payload):
        data = json.loads(payload)
        return {'status': 'BLOCK', 'findings': [], 'evidence_ids': [], 'checked_allergen_ids': data['avoid_ids'],
                'additional_check_needed': False, 'unresolved_allergen_ids': [], 'reason': 'status only'}, model
    monkeypatch.setattr(rag, '_generate', generate)
    p = {'ingredients': '취급 시', 'allergy_info': '밀 함유'}
    assert rag.assess(p, ['새우'])['status'] == 'PASS'
    def unresolved(version, model, payload):
        answer, model = generate(version, model, payload)
        answer['unresolved_allergen_ids'] = answer['checked_allergen_ids']
        return answer, model
    monkeypatch.setattr(rag, '_generate', unresolved)
    assert rag.assess(p, ['새우'])['status'] == 'UNVERIFIED'
