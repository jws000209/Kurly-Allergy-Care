from app import agent


def test_friendly_reply_preserves_facts_and_warning():
    state = {"constraints": {"profiles": ["아이"], "avoid": ["우유"], "strict": True, "budget": None},
             "cart": [{"id": "x"}], "notes": ["우유 혼입 가능 상품은 제외했어요."]}
    concise = agent.respond(state)["reply"]
    friendly = agent.respond({**state, "response_style": "friendly"})["reply"]
    assert concise in friendly
    assert "예산을 조정" in friendly and "안전" not in friendly


def test_style_per_request_and_validation():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        uid = client.post("/api/login", json={"name": "style-test"}).json()["id"]
        payload = {"user_id": uid, "thread_id": "style", "message": "간식 2개", "selected_member_ids": []}
        first = client.post("/api/chat", json={**payload, "response_style": "friendly"}).json()
        second = client.post("/api/chat", json={**payload, "response_style": "concise"}).json()
        assert first["response_style"] == "friendly" and second["response_style"] == "concise"
        assert "예산을 조정" in first["reply"] and "예산을 조정" not in second["reply"]
        assert client.post("/api/chat", json={**payload, "response_style": "invalid"}).status_code == 422
