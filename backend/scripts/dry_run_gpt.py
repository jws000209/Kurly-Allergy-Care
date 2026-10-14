"""GPT wiring dry run: no network calls and no database writes."""
import contextlib
import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from app import agent, intent, selector


def main():
    checks = {}
    models = intent.llm_models()
    checks["local_key_loaded"] = intent.OPENAI_KEY_PATH.is_file() and bool(intent._openai_key())
    checks["gpt_only"] = len(models) == 1 and models[0].startswith("openai:gpt-")
    calls = []

    class FakeGPT(RunnableLambda):
        def with_structured_output(self, schema):
            if schema is intent.ShoppingIntent:
                return RunnableLambda(lambda _: schema(
                    action="create_cart", category_or_purpose="snack",
                    budget=10000, budget_mode="max", item_count=2,
                ))
            return RunnableLambda(lambda _: schema(picks=[selector.Pick(no=2, why="dry run")]))

    def create(model, **kwargs):
        calls.append((model, bool(kwargs.get("api_key"))))
        return FakeGPT(lambda _: AIMessage(content="dry run answer"))

    # Patch the actual client factory, preserving all production routing code.
    with patch.dict(intent._llms, {}, clear=True), patch("langchain.chat_models.init_chat_model", create):
        parsed, parser = intent.parse("snack 2 items 10000", [], [])
        checks["structured_intent"] = parser == "llm" and parsed["item_count"] == 2 and parsed["budget"] == 10000
        candidates = [dict(id=str(i), name=f"item{i}", price=1000*i, category="snack", score=1)
                      for i in range(1, 4)]
        picked = selector.select(candidates, 1, 10000, "snack", None, [])
        checks["product_selection"] = picked is not None and picked[0][0]["id"] == "2" and picked[2] == models[0]
        checks["general_answer"] = agent.answer({"message": "hello"})["reply"] == "dry run answer"
        checks["client_receives_key"] = bool(calls) and all(model in models and has_key for model, has_key in calls)

    with patch.object(intent, "_get", side_effect=RuntimeError("simulated failure")), contextlib.redirect_stdout(io.StringIO()):
        parsed, parser = intent.parse("snack 2 items 10000", [], [])
        checks["rule_fallback"] = parser == "rule-fallback"
        checks["selection_fallback"] = selector.select(candidates, 1, 10000, "snack", None, []) is None
        checks["answer_fallback"] = agent.answer({"message": "hello"})["reply"] == agent.HELP_TEXT

    print(json.dumps({"model": models, "checks": checks, "passed": sum(checks.values()),
                      "total": len(checks), "network_calls": 0, "database_writes": 0}, indent=2))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
