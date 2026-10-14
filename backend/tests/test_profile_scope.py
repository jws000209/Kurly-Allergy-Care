import pytest
from app import profiles


@pytest.fixture
def family():
    return [{"id": 1, "name": "첫째 아들", "relation": "아들", "order": "첫째", "aliases": ["민준"], "allergens": ["우유"], "strict": False},
            {"id": 2, "name": "둘째 딸", "relation": "딸", "order": "둘째", "aliases": [], "allergens": ["땅콩"], "strict": True},
            {"id": 3, "name": "아빠", "relation": "아빠", "order": "", "aliases": [], "allergens": ["밀"], "strict": False},
            {"id": 4, "name": "할머니", "relation": "할머니", "order": "첫째", "aliases": [], "allergens": [], "strict": False}]


@pytest.mark.parametrize("word", ["아이", "우리 애", "형제", "남매", "아이만"])
def test_common_children(word, family):
    r = profiles.resolve([word], family)
    assert {m["id"] for _, m, _ in r["matched"]} == {1, 2}
    assert not r["ambiguous"]


def test_relation_and_particles(family):
    assert profiles.relation_of({"name": "딸기"}) == ""
    assert profiles.resolve(["민준이한테"], family)["matched"][0][1]["id"] == 1
    assert profiles.resolve(["아빠만"], family)["matched"][0][1]["id"] == 3
    assert profiles.resolve(["아기"], family)["ambiguous"]


def test_count_exclusion_and_collision(family):
    r, scope = profiles.resolve_scope("아빠 빼고 가족 간식", ["가족", "아빠"], family)
    assert 3 not in {m["id"] for _, m, _ in r["matched"]}
    assert not scope["issues"]
    r, scope = profiles.resolve_scope("아이 한 명 간식", [], family)
    assert scope["issues"]
    collision = family + [{"id": 5, "name": "가족", "relation": "기타", "aliases": []}]
    assert profiles.resolve(["가족"], collision)["ambiguous"]
    assert profiles.resolve_scope("아이 12개 간식", [], family)[1]["expected_count"] is None
    assert profiles.resolve_scope("아들 둘 간식", [], family)[1]["issues"]
    uncertain = family + [{"id": 5, "name": "막내", "relation": "", "order": "막내", "aliases": []}]
    assert profiles.resolve(["아이"], uncertain)["ambiguous"]


def test_shared_cart_and_pending_count():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        uid = client.post("/api/login", json={"name": "scope-family"}).json()["id"]
        for name, relation, allergy, strict in [("첫째", "아들", "우유", False), ("둘째", "딸", "땅콩", True), ("셋째", "아들", "밀", False)]:
            client.post(f"/api/users/{uid}/members", json={"name": name, "relation": relation, "allergens": [allergy], "strict": strict})
        def say(message, thread="family"):
            return client.post("/api/chat", json={"user_id": uid, "thread_id": thread, "message": message}).json()
        common = say("우리 애 간식 2개")
        assert not common["asked"] and set(common["constraints"]["avoid"]) == {"우유", "땅콩", "밀"}
        assert common["constraints"]["strict"] and "공통 추천" in common["reply"]
        assert say("아이 둘 간식 2개", "count")["asked"]
        assert say("응", "count")["asked"]
        assert not say("첫째랑 둘째", "count")["asked"]
        assert say("삼촌 빼고 가족 간식 2개", "exclude")["asked"]
        result = say("셋째", "exclude")
        assert not result["asked"] and result["constraints"]["profiles"] == ["첫째", "둘째"]
