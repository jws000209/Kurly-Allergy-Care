import pytest
from app import profiles


@pytest.mark.parametrize('word', ['민준', '민준이', '민준이한테', '민준만'])
def test_short_name_and_particles_reach_scope(word):
    family = [{'id': 1, 'name': '김민준', 'relation': '아들'}]
    result, _ = profiles.resolve_scope(word + ' 먹을 간식 추천', [], family)
    assert {m['id'] for _, m, _ in result['matched']} == {1}
    assert not result['ambiguous'] and not result['unknown']


def test_shared_given_name_and_exact_name_collision_ask():
    family = [{'id': 1, 'name': '김민준'}, {'id': 2, 'name': '이민준'}]
    assert profiles.resolve(['민준'], family)['ambiguous']
    family[1]['name'] = '민준'
    assert profiles.resolve(['민준'], family)['ambiguous']
    assert profiles.resolve(['김민준'], family)['matched'][0][1]['id'] == 1


def test_compound_surname_and_conservative_boundaries():
    assert '민수' in profiles.auto_aliases('남궁민수')
    assert '궁민' not in profiles.auto_aliases('남궁민')
    assert '가족' not in profiles.auto_aliases('김가족')
    assert '철수' not in profiles.auto_aliases('산타철수')
    r, _ = profiles.resolve_scope('민준초콜릿 추천', [], [{'id': 1, 'name': '김민준'}])
    assert not r['matched']
