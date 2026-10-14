from app import intent


def test_local_key_overrides_environment(tmp_path, monkeypatch):
    path = tmp_path / ".openai_api_key"
    path.write_text("\ufefflocal-test-key\n", encoding="utf-8")
    monkeypatch.setattr(intent, "OPENAI_KEY_PATH", path)
    monkeypatch.setenv("OPENAI_API_KEY", "environment-test-key")
    monkeypatch.setenv("LLM_MODEL", "groq:other-model")
    assert intent._openai_key() == "local-test-key"
    assert intent.llm_models() == ["openai:gpt-6-luna"]


def test_environment_key_and_gpt_override(tmp_path, monkeypatch):
    monkeypatch.setattr(intent, "OPENAI_KEY_PATH", tmp_path / "missing")
    monkeypatch.setenv("OPENAI_API_KEY", "environment-test-key")
    monkeypatch.setenv("LLM_MODEL", "gpt-4o-mini")
    assert intent._openai_key() == "environment-test-key"
    assert intent.llm_models() == ["openai:gpt-4o-mini"]


def test_other_provider_keys_do_not_enable_llm(tmp_path, monkeypatch):
    monkeypatch.setattr(intent, "OPENAI_KEY_PATH", tmp_path / "missing")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("GROQ_API_KEY", "other-test-key")
    monkeypatch.setenv("GOOGLE_API_KEY", "other-test-key")
    monkeypatch.setenv("LLM_MODEL", "openai:gpt-4o-mini")
    assert intent.llm_models() == []


def test_luna_client_options(monkeypatch):
    import langchain.chat_models
    calls = []
    monkeypatch.setattr(intent, "_llms", {})
    monkeypatch.setattr(intent, "_openai_key", lambda: "test-key")
    monkeypatch.setattr(langchain.chat_models, "init_chat_model", lambda model, **kw: calls.append((model, kw)))
    intent._get("openai:gpt-6-luna")
    assert calls[0][1]["reasoning_effort"] == "none"
    assert "temperature" not in calls[0][1]
