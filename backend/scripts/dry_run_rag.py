"""Read the real workbook and exercise RAG with fake GPT; no DB or network writes."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from app import intent, allergen_rag as rag
from app.allergen_dictionary import get_dictionary, dictionary_path
from app.extractor import extract
from app.rule_engine import judge


def main():
    os.environ["KAC_ALLERGY_PROVIDER"] = "rag"
    path = dictionary_path()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    d = get_dictionary()
    checks = {"luna_selected": intent.llm_models() == ["openai:gpt-6-luna"],
              "local_key_loaded": bool(intent._openai_key()),
              "master_39": len(d.master) == 39, "exceptions_20": len(d.exceptions) == 20}

    def make(text, label=""):
        return {"ingredients": text, "allergy_label": label, **extract(text, label)}

    def fake(build, payload):
        data = json.loads(payload["data"])
        return rag.Assessment(status="PASS", checked_allergen_ids=data["avoid_ids"],
                              additional_check_needed=False, reason="mock review"), "openai:gpt-6-luna"

    with patch.object(intent, "invoke_with_fallback", fake):
        rag._generate.cache_clear()
        checks["grounded_pass"] = judge(make("쌀, 설탕"), ["우유"])["rag"]["mode"] == "generated"
        checks["milk_block"] = judge(make("whey protein"), ["우유"])["status"] == "BLOCK"
        checks["ambiguous_review"] = judge(make("lecithin"), ["대두"])["status"] == "UNVERIFIED"
        checks["negative_review"] = judge(make("우유 함유 없음"), ["우유"])["status"] == "UNVERIFIED"
        checks["strict_cross_block"] = judge(make("쌀", "우유와 같은 제조시설"), ["우유"], True)["status"] == "BLOCK"
        checks["official_expanded_review"] = judge(make("", "우유 함유"), ["사과"])["status"] == "UNVERIFIED"
        checks["cashew_not_milk"] = "우유" not in extract("캐슈밀크", "")["contains"]
        checks["all_39_detected"] = all(a["name"] in extract(next(str(e["raw"]["원재료_매칭어"]) for e in d.entries
                                                   if e["allergen_id"] == aid and e["automatic"]), "")["contains"]
                                           for aid, a in d.master.items())

    rag._generate.cache_clear()
    with patch.object(intent, "invoke_with_fallback", side_effect=RuntimeError("mock outage")), contextlib.redirect_stdout(io.StringIO()):
        checks["failure_closed"] = judge(make("쌀, 설탕"), ["우유"])["status"] == "UNVERIFIED"
    checks["workbook_unchanged"] = hashlib.sha256(path.read_bytes()).hexdigest() == digest
    print(json.dumps({"model": intent.llm_models(), "dictionary": d.filename, "sha256": digest,
                      "entries": len(d.entries), "missing_policy_entries": sum(not e["raw"].get("신뢰등급") for e in d.entries),
                      "checks": checks, "passed": sum(checks.values()), "total": len(checks),
                      "network_calls": 0, "database_writes": 0}, ensure_ascii=False, indent=2))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
