"""알고리즘의 순위·대체 동작과 실제 API/대화 연결을 검증한다. 외부 LLM은 호출하지 않는다."""
import json

import pytest
from fastapi.testclient import TestClient

from app import agent, cart_builder, db, intent, recommender
from app.main import app


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "recommend.sqlite3")
    monkeypatch.setattr(db, "SEED_PATH", tmp_path / "seed.json")
    monkeypatch.setattr(db, "LIVE_MIN_EA_PATH", tmp_path / "min.json")
    monkeypatch.setattr(intent, "llm_models", lambda: [])
    products = [
        {"id": "a", "name": "사과 주스", "category": "음료>주스", "ingredients": "사과, 정제수", "review_count": 5},
        {"id": "b", "name": "사과 착즙 주스", "category": "음료>주스", "ingredients": "사과, 정제수", "min_ea": 2},
        {"id": "c", "name": "탄산 콜라", "category": "음료>주스", "ingredients": "정제수, 탄산", "review_count": 900},
        {"id": "milk", "name": "사과 우유 주스", "category": "음료>주스", "ingredients": "사과, 우유"},
        {"id": "cross", "name": "사과 주스 혼입", "category": "음료>주스", "ingredients": "사과",
         "allergy_label": "해당 없음. 우유를 사용한 제품과 같은 제조시설"},
        {"id": "unknown", "name": "사과 주스 미확인", "category": "음료>주스", "ingredients": "", "allergy_label": ""},
    ]
    products = [{"price": 1000, "allergy_label": "해당 없음", "source": "kurly", **p} for p in products]
    db.SEED_PATH.write_text(json.dumps(products, ensure_ascii=False), encoding="utf-8")
    db.init_db()
    user = db.login("추천 사용자")
    other = db.login("유사 사용자")
    member = db.create_member(user["id"], "본인", ["우유"], True)
    return user, other, member


def test_item_axis_and_weighted_average():
    matrix = {1: {"a": 5}, 2: {"a": 5, "b": 5, "c": 1}, 3: {"x": 5, "c": 5}}
    scores = recommender.item_scores(matrix[1], matrix)
    assert scores["b"] == 1 and scores["c"] == 1  # 예시의 가중 평균: 단일 이웃이면 동일 예측값.
    assert "x" not in scores
    assert recommender.item_scores({}, matrix) == {}
    assert recommender.item_scores({"a": 5}, {1: {"a": 5}}) == {}
    mixed = recommender.item_scores({"a": 5, "d": 1}, {1: {"a": 5, "d": 1, "b": 5}})
    assert mixed["b"] == pytest.approx(0.6)


def test_bought_this_also_bought_uses_only_purchases():
    rows = [{"user_id": uid, "product_id": pid, "event": event} for uid, pid, event in
            [(1, "a", "purchase"), (1, "self", "purchase"),
             (2, "a", "purchase"), (2, "b", "purchase"), (2, "c", "cart"),
             (3, "a", "purchase"), (3, "b", "purchase"), (3, "d", "purchase"),
             (4, "a", "view"), (4, "c", "purchase")]]
    scores = recommender.co_purchase_scores({"a"}, rows, 1)
    assert scores == {"b": 1.0, "d": 0.5}
    assert recommender.co_purchase_scores({"missing"}, rows, 1) == {}
    assert recommender.co_purchase_scores(set(), rows, 1) == {}


def test_deduplicated_history_and_persistence(catalog):
    user, _, _ = catalog
    db.record_interaction(user["id"], "a", "view")
    db.record_interaction(user["id"], "a", "view")
    db.record_interaction(user["id"], "a", "purchase")
    db.init_db()
    assert len(db.list_interactions()) == 2
    assert recommender.interaction_matrix(db.list_interactions())[user["id"]] == {"a": 5}


def test_content_similarity_and_cache_invalidation():
    products = [{"id": "a", "name": "사과 주스", "category": "음료", "ingredients": "사과"},
                {"id": "b", "name": "사과 착즙 주스", "category": "음료", "ingredients": "사과"},
                {"id": "c", "name": "소고기 구이", "category": "정육", "ingredients": "소고기"}]
    scores = recommender.content_scores(products, {"a": 5})
    assert scores["b"] > scores["c"]
    assert scores["a"] == pytest.approx(1)
    products[1].update(name="수건", category="생활", ingredients="면")
    assert recommender.content_scores(products, {"a": 5})["b"] < scores["b"]
    assert recommender.content_scores([], {"a": 5}) == {}
    assert recommender.content_scores(products, {"missing": 5}) == {}


def test_cold_start_and_user_fallback(catalog):
    user, _, _ = catalog
    scores = recommender.score_products(db.list_products(), [], user["id"])
    assert all(s["recommendation_method"] == "popularity" for s in scores.values())
    db.record_interaction(user["id"], "a", "like")
    scores = recommender.score_products(db.list_products(), db.list_interactions(), user["id"], "user")
    assert scores["b"]["recommendation_method"] == "content"
    assert scores["b"]["recommendation_score"] > scores["c"]["recommendation_score"]


def test_api_safety_budget_reference_and_seen(catalog):
    user, other, member = catalog
    for uid, pid in [(user["id"], "a"), (other["id"], "a"), (other["id"], "milk"), (other["id"], "b")]:
        db.record_interaction(uid, pid, "purchase")
    client = TestClient(app)
    body = {"user_id": user["id"], "member_ids": [member["id"]], "mode": "user", "count": 5, "budget": 2000}
    response = client.post("/api/recommendations", json=body)
    assert response.status_code == 200
    result = response.json()
    assert result["items"] and result["items"][0]["id"] == "b"
    assert sum(i["price"] for i in result["items"]) <= 2000
    assert {i["id"] for i in result["items"]}.isdisjoint({"a", "milk", "cross", "unknown"})
    assert result["items"][0]["price"] == 2000  # 최소 구매 수량 반영.
    result = client.post("/api/recommendations", json={**body, "mode": "content", "reference_product_id": "a",
                                                      "exclude_seen": False, "count": 1}).json()
    assert result["items"][0]["id"] == "b"
    assert result["items"][0]["recommendation_method"] == "content"
    result = client.post("/api/recommendations", json={**body, "mode": "item", "reference_product_id": "a",
                                                      "count": 1}).json()
    assert result["items"][0]["id"] == "b"
    assert result["items"][0]["recommendation_method"] == "item"
    assert result["items"][0]["item_score"] == 1


def test_api_validation(catalog):
    user, other, _ = catalog
    client = TestClient(app)
    path = f"/api/users/{user['id']}/interactions"
    assert client.post(path, json={"product_id": "missing", "event": "view"}).status_code == 404
    assert client.post(path, json={"product_id": "a", "event": "invalid"}).status_code == 422
    assert client.post("/api/users/999/interactions", json={"product_id": "a", "event": "view"}).status_code == 404
    foreign = db.create_member(other["id"], "다른 가족", [], False)
    body = {"user_id": user["id"]}
    for patch, code in [({"count": 0}, 422), ({"mode": "other"}, 422), ({"budget": 0}, 422),
                        ({"reference_product_id": "missing"}, 404), ({"user_id": 999}, 404),
                        ({"member_ids": [foreign["id"]]}, 422)]:
        assert client.post("/api/recommendations", json={**body, **patch}).status_code == code
    assert client.post(path, json={"product_id": "a", "event": "like"}).status_code == 200


def test_chat_uses_personalization_and_preserves_explicit_price_sort(catalog):
    user, other, member = catalog
    for uid, pid in [(user["id"], "a"), (other["id"], "a"), (other["id"], "b")]:
        db.record_interaction(uid, pid, "purchase")
    items, _ = cart_builder.search(["주스"], ["우유"], True, user_id=user["id"])
    assert [i["id"] for i in items].index("b") < [i["id"] for i in items].index("c")
    items, _ = cart_builder.search(["주스"], ["우유"], True, user_id=user["id"], cheap_first=True)
    assert items[0]["price"] == 1000
    result = agent.chat(user["id"], "personalized", "주스 2개", [member["id"]])
    assert {i["id"] for i in result["cart"]} == {"a", "b"}  # 장보기 대화에서는 재구매 후보도 유지.
    assert any(i["co_purchase_score"] > 0 for i in result["cart"])
    # 같은 thread 문자열을 쓰는 다른 계정으로 대화 상태가 이어지지 않는다.
    other_result = agent.chat(other["id"], "personalized", "주스 1개", [])
    assert other_result["asked"] and not other_result["cart"]
