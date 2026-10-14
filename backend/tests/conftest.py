import pytest
from pathlib import Path


@pytest.fixture(autouse=True)
def isolate_storage(tmp_path, monkeypatch):
    from app import db, agent
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "isolated.sqlite3")
    monkeypatch.setattr(db, "SEED_PATH", Path(__file__).with_name("mock_products.json"))
    monkeypatch.setattr(db, "LIVE_MIN_EA_PATH", tmp_path / "min-ea.json")
    monkeypatch.setattr(agent, "agent", agent.build_graph())


@pytest.fixture(autouse=True)
def isolate_live_services(monkeypatch, request):
    """Legacy regression tests explicitly use the rule comparison provider."""
    monkeypatch.setenv("KAC_ALLERGY_PROVIDER", "rule")
    from app import intent
    if request.module.__name__ != "test_llm_config":
        monkeypatch.setattr(intent, "_openai_key", lambda: "")
