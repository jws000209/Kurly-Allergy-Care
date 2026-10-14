import pytest
from pydantic import ValidationError
from app import intent


def test_mixed_negative_allergy_and_unknown_english():
    parsed, _ = intent.parse("우유 알레르기는 없지만 땅콩 알러지 있어 간식 추천", [], [])
    assert parsed["excluded_allergens"] == ["땅콩"]
    parsed, _ = intent.parse("I am allergic to mango. Recommend snacks.", [], [])
    assert parsed["needs_allergen_confirmation"] and "mango" in parsed["unresolved_mentions"]
    assert intent.parse("우유는 괜찮은데 땅콩은 못 먹어", [], [])[0]["needs_allergen_confirmation"]


def test_group_counts_and_sum_conflict():
    parsed, _ = intent.parse("반찬 2개랑 라면 3개 2만원 이내", [], [])
    assert parsed["item_count"] == 5
    assert [(g["label"], g["count"]) for g in parsed["groups"]] == [("반찬", 2), ("라면", 3)]
    assert intent.parse("반찬 2개랑 라면 3개 총 4개", [], [])[0]["validation_issues"]


def test_edit_keeps_current_budget_and_count():
    parsed, _ = intent.parse("과자 대신 라면 2개 5천원 이내", [], [{"name": "과자"}])
    assert parsed["action"] == "replace_item" and parsed["budget"] == 5000 and parsed["item_count"] == 2
    assert intent.parse("라면 추천해줄래?", [], [])[0]["action"] == "create_cart"
    assert intent._budget_detail("500원 이하")["budget"] == 500
    assert intent._budget_detail("1000원~2000원")["budget_min"] == 1000


def test_ground_actual_values_and_schema_bounds():
    bad = intent.ShoppingIntent(action="create_cart", budget=999999, item_count=29).model_dump()
    result = intent._ground(bad, "간식 3개", [])
    assert result["budget"] is None and result["item_count"] == 3
    with pytest.raises(ValidationError):
        intent.ShoppingIntent(action="create_cart", budget=-1, item_count=-2)
    assert intent.parse("간식 -2개", [], [])[0]["validation_issues"]


def test_pending_profile_merges_new_conditions():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        uid = client.post("/api/login", json={"name": "pending-validation"}).json()["id"]
        client.post(f"/api/users/{uid}/members", json={"name": "첫째", "relation": "아들", "allergens": ["땅콩"]})
        def say(message):
            return client.post("/api/chat", json={"user_id": uid, "thread_id": "pending", "message": message}).json()
        assert say("엄마 간식 2개 1만원 이내")["asked"]
        result = say("첫째 우유도 빼줘 5천원으로 3개")
        assert not result["asked"]
        assert result["constraints"]["budget"]["amount"] == 5000
        assert result["constraints"]["count"] == 3
        assert set(result["constraints"]["avoid"]) == {"우유", "땅콩"}


def test_group_request_api_preserves_each_quantity():
    from fastapi.testclient import TestClient
    from app.main import app
    from app import db
    with TestClient(app) as client:
        # The base mock catalog intentionally has no ramen; provide three distinct
        # available products for the exact-fill integration scenario.
        for number in range(3):
            db.upsert_product({"id": f"ramen-test-{number}", "name": f"시험 라면 {number}", "category": "간편식>라면",
                               "ingredients": "쌀", "price": 1000})
        uid = client.post("/api/login", json={"name": "groups-validation"}).json()["id"]
        result = client.post("/api/chat", json={"user_id": uid, "thread_id": "groups", "message": "반찬 2개랑 라면 3개"}).json()
        assert result["constraints"]["count"] == 5
        assert [g["count"] for g in result["constraints"]["groups"]] == [2, 3]
        assert sum(i.get("group") == "반찬" for i in result["cart"]) == 2
        assert sum(i.get("group") == "라면" for i in result["cart"]) == 3
