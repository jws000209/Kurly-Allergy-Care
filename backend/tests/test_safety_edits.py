"""수정 요청 제약 누락과 오래된 추천의 담기 검증 회귀 시험."""
import json

import pytest
from fastapi.testclient import TestClient

from app import db, intent
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    for key in ("LLM_MODEL", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY", "GROQ_API_KEY"):
        monkeypatch.setenv(key, "")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.sqlite3")
    seed = tmp_path / "seed.json"
    category = "간편식>치킨/피자/핫도그/만두"
    products = [
        {"id": "expensive", "name": "고기만두", "price": 10000, "allergy_label": "밀, 우유 함유", "review_count": 100},
        {"id": "safe", "name": "김치만두", "price": 6000, "allergy_label": "밀 함유", "review_count": 10},
        {"id": "milk", "name": "우유만두", "price": 2000, "allergy_label": "밀, 우유 함유"},
        {"id": "chicken", "name": "치킨너겟", "price": 1000, "allergy_label": "밀, 우유 함유"},
        {"id": "plain-chicken", "name": "순살치킨", "price": 500, "allergy_label": "닭고기 함유"},
        {"id": "cross", "name": "혼입만두", "price": 9000, "allergy_label": "밀 함유. 우유 혼입 가능"},
        {"id": "blank", "name": "미확인만두", "price": 1000, "allergy_label": ""},
    ]
    seed.write_text(json.dumps([{**p, "category": category, "source": "mock"} for p in products]), encoding="utf-8")
    monkeypatch.setattr(db, "SEED_PATH", seed)
    monkeypatch.setattr(db, "LIVE_MIN_EA_PATH", tmp_path / "min-ea.json")
    with TestClient(app) as c:
        yield c


def setup_user(client, name="수정시험", allergens=None):
    user = client.post("/api/login", json={"name": name}).json()
    member = client.post(f"/api/users/{user['id']}/members",
                         json={"name": "본인", "allergens": allergens or ["알류"]}).json()
    return user["id"], member["id"]


@pytest.mark.parametrize("message,action", [
    ("우유 없이 더 저렴하게", "cheaper"),
    ("우유 없이 1번 상품 바꿔줘", "replace_item"),
    ("우유 없이 1번 상품 빼줘", "remove_item"),
])
def test_edit_preserves_new_avoid_constraints(client, message, action):
    uid, mid = setup_user(client)

    def say(text):
        response = client.post("/api/chat", json={"user_id": uid, "thread_id": message,
            "message": text, "selected_member_ids": [mid]})
        assert response.status_code == 200
        return response.json()

    before = say("만두 1개")
    assert before["cart"][0]["id"] == "expensive"
    after = say(message)
    assert after["intent"]["action"] == action
    assert after["constraints"]["avoid"] == ["알류", "우유"]
    for item in after["cart"]:
        assert "우유" not in db.get_product(item["id"])["contains"]
        assert "만두" in item["name"]  # 묶인 분류의 저렴한 치킨으로 대체하지 않는다.
    if action != "remove_item":
        assert after["cart"][0]["id"] == "safe"
    later = say("2개로")
    assert "우유" in later["constraints"]["avoid"]


def test_llm_omission_is_supplemented_and_failure_falls_back(monkeypatch):
    monkeypatch.setattr(intent, "llm_models", lambda: ["test-model"])
    monkeypatch.setattr(intent, "invoke_with_fallback", lambda *args: (intent.ShoppingIntent(action="cheaper"), "test-model"))
    parsed, parser = intent.parse("우유 없이 더 저렴하게", [], [{"name": "고기만두"}])
    assert parser == "llm" and parsed["excluded_allergens"] == ["우유"]

    def fail(*args):
        raise RuntimeError("offline")

    monkeypatch.setattr(intent, "invoke_with_fallback", fail)
    parsed, parser = intent.parse("우유 없이 더 저렴하게", [], [{"name": "고기만두"}])
    assert parser == "rule-fallback" and parsed["excluded_allergens"] == ["우유"]


def test_cart_validation_uses_current_profile_and_extra_exclusions(client):
    uid, mid = setup_user(client)
    body = {"user_id": uid, "member_ids": [mid], "product_id": "milk"}
    assert client.post("/api/cart/validate", json=body).json()["status"] == "PASS"
    # 추천 후 프로필 내용이 바뀌면 과거 PASS를 믿지 않는다.
    client.put(f"/api/members/{mid}", json={"name": "본인", "allergens": ["우유"]})
    result = client.post("/api/cart/validate", json=body)
    assert result.status_code == 200 and result.json()["status"] == "BLOCK"
    client.put(f"/api/members/{mid}", json={"name": "본인", "allergens": ["알류"]})
    result = client.post("/api/cart/validate", json={**body, "excluded_allergens": ["우유"]})
    assert result.json()["status"] == "BLOCK"
    assert client.post("/api/cart/validate", json={**body, "product_id": "blank"}).json()["status"] == "UNVERIFIED"
    assert client.post("/api/cart/validate", json={**body, "product_id": "cross"}).json()["status"] == "PASS"
    client.put(f"/api/members/{mid}", json={"name": "본인", "allergens": ["우유"], "strict": True})
    assert client.post("/api/cart/validate", json={**body, "product_id": "cross"}).json()["status"] == "BLOCK"
    # 검증 API는 상품을 덮어쓰거나 행동을 기록하지 않는다.
    assert db.get_product("milk")["price"] == 2000 and db.list_interactions() == []


def test_cart_validation_rejects_missing_or_foreign_context(client):
    uid, mid = setup_user(client)
    _, other = setup_user(client, "다른시험")
    body = {"user_id": uid, "member_ids": [mid], "product_id": "safe"}
    for update, status in [({"user_id": -1}, 404), ({"product_id": "missing"}, 404),
                           ({"member_ids": [other]}, 422), ({"member_ids": []}, 422),
                           ({"excluded_allergens": ["unknown"]}, 422)]:
        assert client.post("/api/cart/validate", json={**body, **update}).status_code == status
    client.delete(f"/api/members/{mid}")
    assert client.post("/api/cart/validate", json=body).status_code == 422
