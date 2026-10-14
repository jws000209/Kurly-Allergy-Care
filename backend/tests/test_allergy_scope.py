import pytest
from app import intent
from app.allergy_scope import enforce, scan
from app.rule_engine import judge


@pytest.mark.parametrize("word", ["갑각류", "crustaceans"])
def test_crustaceans_expand(word):
    result = enforce({"excluded_allergens": []}, f"{word} 알러지 있어 간식 추천해줘")
    assert set(result["excluded_allergens"]) == {"게", "새우"}
    assert not result["needs_allergen_confirmation"]


@pytest.mark.parametrize("word", ["견과류", "생선", "어류", "해산물", "shellfish", "tree nuts"])
def test_broad_scope_requires_confirmation(word):
    assert scan(f"{word} 알레르기 있어")["unresolved_mentions"]
    assert intent._clean({"excluded_allergens": [word]})["needs_allergen_confirmation"]


def test_unknown_preserved():
    result = intent._clean({"excluded_allergens": ["망고"]})
    assert result["unresolved_mentions"] == ["망고"]


def test_negative_and_only_conditions():
    result = enforce({"excluded_allergens": ["새우"]}, "새우는 괜찮고 게만 알러지 있어")
    assert result["excluded_allergens"] == ["게"]
    result = enforce({"excluded_allergens": ["게"]}, "게 알레르기는 없고 새우만 피하고 싶어")
    assert result["excluded_allergens"] == ["새우"]
    assert scan("갑각류 알러지인데 새우는 괜찮아")["unresolved_mentions"]


def test_mixed_supported_and_unsupported():
    result = scan("망고랑 사과 알러지 있어")
    assert "망고" in result["unresolved_mentions"] and "사과" in result["avoid"]


@pytest.mark.parametrize("message,expected", [("우유 알러지 있어 간식 추천해줘", ["우유"]),
                                             ("대하 알레르기 있어", ["새우"])])
def test_rule_fallback_enforces_scope(message, expected):
    result, parser = intent.parse(message, [], [])
    assert result["excluded_allergens"] == expected


@pytest.mark.parametrize("text,avoid", [("갑각류 함유", "새우"), ("crustaceans", "게"), ("해산물", "연어")])
def test_product_broad_scope_not_pass(monkeypatch, text, avoid):
    monkeypatch.setenv("KAC_ALLERGY_PROVIDER", "rag")
    assert judge({"ingredients": text}, [avoid], generate=False)["status"] == "UNVERIFIED"


def test_clarification_persists_until_specific_answer():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        uid = client.post("/api/login", json={"name": "scope"}).json()["id"]
        def say(message):
            return client.post("/api/chat", json={"user_id": uid, "thread_id": "scope", "message": message}).json()
        assert say("견과류 알러지 있어 간식 2개 추천해줘")["asked"]
        assert say("응")["asked"]
        answer = say("호두만")
        assert not answer["asked"] and answer["constraints"]["avoid"] == ["호두"]
