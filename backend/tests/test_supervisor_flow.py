"""관리자(supervisor) 노드 도입 후 실행 기록이 기존 분기와 맞는지 확인한다."""
from fastapi.testclient import TestClient

from app import intent as intent_parser
from app.main import app


def _chat(client, user_id, thread, message, member_ids):
    return client.post("/api/chat", json={"user_id": user_id, "thread_id": thread, "message": message,
                                          "selected_member_ids": member_ids}).json()


def test_recommendation_goes_through_supervisor_to_build_cart(monkeypatch):
    monkeypatch.setattr(intent_parser, "llm_models", lambda: [])
    with TestClient(app) as client:
        user = client.post("/api/login", json={"name": "관리자흐름"}).json()
        kid = client.post(f"/api/users/{user['id']}/members", json={"name": "첫째", "allergens": ["우유"]}).json()
        result = _chat(client, user["id"], "flow1", "간식 2개", [kid["id"]])
        path = result["workflow"]
        assert path[:3] == ["parse_intent", "resolve_conditions", "supervisor"]
        assert "build_cart" in path and path.index("supervisor") < path.index("build_cart")
        assert path[-1] == "remember"


def test_profile_question_is_routed_by_supervisor_without_building(monkeypatch):
    monkeypatch.setattr(intent_parser, "llm_models", lambda: [])
    with TestClient(app) as client:
        user = client.post("/api/login", json={"name": "관리자질문"}).json()
        result = _chat(client, user["id"], "flow2", "간식 2개", [])
        path = result["workflow"]
        assert "supervisor" in path and "build_cart" not in path
        assert "ask_profile" in path and path[-1] == "remember"
