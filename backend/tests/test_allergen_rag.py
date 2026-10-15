import hashlib
from pathlib import Path

import pytest

from app import allergen_rag as rag, intent
from app.allergen_dictionary import get_dictionary, dictionary_path
from app.extractor import extract
from app.rule_engine import judge


@pytest.fixture(autouse=True)
def rag_provider(monkeypatch):
    monkeypatch.setenv("KAC_ALLERGY_PROVIDER", "rag")
    monkeypatch.delenv("KAC_ALLERGEN_DICTIONARY", raising=False)
    rag._generate.cache_clear()


def product(ingredients="쌀, 설탕", label=""):
    return {"ingredients": ingredients, "allergy_label": label, **extract(ingredients, label)}


def mock_answer(monkeypatch, **overrides):
    monkeypatch.setattr(intent, "llm_models", lambda: ["openai:gpt-6-luna"])

    def invoke(build, payload):
        import json
        data = json.loads(payload["data"])
        assert data["avoid_allergens"] and data["avoid_allergens"][0]["name"]
        return rag.Assessment(status="PASS", checked_allergen_ids=data["avoid_ids"],
                              additional_check_needed=False, reason="근거 검토", **overrides), "openai:gpt-6-luna"
    monkeypatch.setattr(intent, "invoke_with_fallback", invoke)


def test_workbook_provenance_and_missing_policy():
    path = dictionary_path()
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    d = get_dictionary()
    assert len(d.master) == 39 and len(d.exceptions) == 20
    assert d.version == before == hashlib.sha256(path.read_bytes()).hexdigest()
    missing = [e for e in d.entries if not e["raw"].get("신뢰등급")]
    assert all(not e["automatic"] for e in missing)
    assert all(e["sheet"] and e["row"] > 1 and e["id"] for e in d.entries)


def test_all_39_have_automatic_evidence():
    d = get_dictionary()
    for aid, master in d.master.items():
        entry = next(e for e in d.entries if e["allergen_id"] == aid and e["automatic"])
        assert master["name"] in extract(str(entry["raw"]["원재료_매칭어"]), "")["contains"]


@pytest.mark.parametrize("text,expected", [
    ("cashew milk", "캐슈넛"), ("soy lecithin", "대두"), ("wheat flour", "밀"),
    ("루핀콩", "루핀"), ("바나나밀", "바나나"), ("카제인", "우유"),
])
def test_multilingual_retrieval(text, expected):
    verdict = judge(product(text), [expected])
    assert verdict["status"] == "BLOCK"
    assert any(e["name"] == expected for e in verdict["rag"]["evidence"])


@pytest.mark.parametrize("text,avoid", [("캐슈밀크", "우유"), ("루핀콩", "대두"),
                                        ("바나나밀", "밀"), ("eggplant", "알류")])
def test_substring_false_positives(text, avoid):
    assert avoid not in extract(text, "")["contains"]


def test_longer_match_does_not_hide_separate_ingredient():
    assert "우유" in extract("cashew milk, milk", "")["contains"]
    assert "밀" in extract("바나나밀, 밀", "")["contains"]


def test_raw_apple_and_incomplete_ocr():
    from app.extractor import NO_LABEL_REVIEWED
    assert judge({"name": "저탄소 사과", "allergy_label": NO_LABEL_REVIEWED}, ["사과"])["status"] == "BLOCK"
    assert judge(product("비타민C 직사광선을 피해 보관하십시오"), ["사과"])["status"] == "UNVERIFIED"


def test_exception_without_automatic_origin():
    result = judge(product("전분"), ["밀"])
    assert result["status"] == "UNVERIFIED"
    assert any(e["id"] == "E007" for e in result["rag"]["exceptions"])


@pytest.mark.parametrize("text,avoid", [("레시틴", "대두"), ("cream", "우유"),
                                        ("collagen", "젤라틴"), ("우유 함유 없음", "우유")])
def test_ambiguous_or_negative_evidence_is_not_pass(text, avoid):
    assert judge(product(text), [avoid])["status"] == "UNVERIFIED"


def test_cross_contamination_and_flavour():
    p = product("쌀, 설탕", "우유를 사용하는 제품과 같은 제조시설")
    assert p["contains"] == [] and p["cross"] == ["우유"]
    assert judge(p, ["우유"], strict=True)["status"] == "BLOCK"
    assert "사과" not in extract("사과향", "")["contains"]


def test_generated_pass_and_context(monkeypatch):
    mock_answer(monkeypatch)
    result = judge(product(), ["우유"])
    assert result["status"] == "PASS" and result["rag"]["mode"] == "generated"
    assert result["rag"]["model"] == "openai:gpt-6-luna"


def test_hallucinated_citation_rejected(monkeypatch):
    mock_answer(monkeypatch, evidence_ids=["D999999"])
    result = judge(product(), ["우유"])
    assert result["status"] == "UNVERIFIED" and result["rag"]["mode"] == "generation-failed"


def test_wrong_quote_or_missing_coverage_rejected(monkeypatch):
    monkeypatch.setattr(intent, "llm_models", lambda: ["openai:gpt-6-luna"])
    monkeypatch.setattr(intent, "invoke_with_fallback", lambda *a: (
        rag.Assessment(status="PASS", checked_allergen_ids=[], additional_check_needed=False,
                       reason="fake"), "openai:gpt-6-luna"))
    assert judge(product(), ["우유"])["status"] == "UNVERIFIED"


def test_workbook_change_invalidates_version(tmp_path, monkeypatch):
    from openpyxl import load_workbook
    import shutil
    copied = tmp_path / "dictionary.xlsx"
    shutil.copyfile(dictionary_path(), copied)
    monkeypatch.setenv("KAC_ALLERGEN_DICTIONARY", str(copied))
    first = get_dictionary().version
    workbook = load_workbook(copied)
    workbook["사용가이드"]["B2"] = "test-only changed dictionary"
    workbook.save(copied)
    workbook.close()
    assert get_dictionary().version != first


def test_failure_never_promotes_pass(monkeypatch):
    monkeypatch.setattr(intent, "llm_models", lambda: ["openai:gpt-6-luna"])
    monkeypatch.setattr(intent, "invoke_with_fallback", lambda *a: (_ for _ in ()).throw(RuntimeError("mock")))
    assert judge(product(), ["우유"])["status"] == "UNVERIFIED"
    assert judge(product("우유"), ["우유"])["status"] == "BLOCK"


def test_missing_dictionary_fail_closed(monkeypatch, tmp_path):
    monkeypatch.setenv("KAC_ALLERGEN_DICTIONARY", str(tmp_path / "missing.xlsx"))
    assert judge({"ingredients": "쌀", "verified": True}, ["우유"])["status"] == "UNVERIFIED"
    assert judge({"contains": ["우유"], "verified": True}, ["우유"])["status"] == "BLOCK"


def test_corrupt_dictionary_fails_closed(monkeypatch, tmp_path):
    path = tmp_path / "bad.xlsx"
    path.write_bytes(b"not an xlsx")
    monkeypatch.setenv("KAC_ALLERGEN_DICTIONARY", str(path))
    assert judge(product("쌀"), ["우유"])["status"] == "UNVERIFIED"


def test_unknown_avoid_is_never_a_search_pass():
    assert judge(product(), ["unknown"], generate=False)["status"] == "UNVERIFIED"


def test_search_gate_does_not_call_gpt(monkeypatch):
    monkeypatch.setattr(intent, "invoke_with_fallback", lambda *a: pytest.fail("unexpected GPT call"))
    assert judge(product(), ["우유"], generate=False)["status"] == "PASS"
    assert judge(product(), ["우유"])["status"] == "UNVERIFIED"


def test_official_label_cannot_clear_expanded_allergen():
    assert judge(product("", "우유 함유"), ["사과"])["status"] == "UNVERIFIED"


def test_http_check_uses_rag(monkeypatch, tmp_path):
    from app import db
    from app.main import app
    from fastapi.testclient import TestClient
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.sqlite3")
    monkeypatch.setattr(db, "SEED_PATH", tmp_path / "missing.json")
    mock_answer(monkeypatch)
    with TestClient(app) as client:
        user = client.post("/api/login", json={"name": "rag-test"}).json()
        member = client.post(f"/api/users/{user['id']}/members", json={"name": "tester", "allergens": ["우유"]}).json()
        response = client.post("/api/check", json={"user_id": user["id"], "member_ids": [member["id"]], "save": False,
            "product": {"id": "rag-test", "name": "rice", "price": 1000, "ingredients": "쌀, 설탕"}})
        assert response.status_code == 200
        assert "rag" not in response.json()
        assert "GPT" not in response.json()["reason"]
        assert db.get_product("rag-test") is None


def test_cart_search_and_final_revalidation(monkeypatch, tmp_path):
    from app import db, cart_composer
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "cart.sqlite3")
    monkeypatch.setattr(db, "SEED_PATH", tmp_path / "missing.json")
    db.init_db()
    db.upsert_product({"id": "rice", "name": "쌀과자", "price": 1000, "category": "간식>과자", "ingredients": "쌀, 설탕"})
    db.upsert_product({"id": "milk", "name": "우유과자", "price": 1000, "category": "간식>과자", "ingredients": "우유"})
    monkeypatch.setattr(intent, "invoke_with_fallback", lambda *a: pytest.fail("candidate search called GPT"))
    candidates, stats = cart_composer.search(["과자"], ["우유"], False)
    assert [c["id"] for c in candidates] == ["rice"]
    mock_answer(monkeypatch)
    diagnostics = []
    items, _ = cart_composer.revalidate(candidates, ["우유"], False, diagnostics=diagnostics)
    assert len(items) == 1 and "rag" not in items[0]
    assert diagnostics[0]["verdict"]["rag"]["mode"] == "generated"
    rag._generate.cache_clear()
    monkeypatch.setattr(intent, "invoke_with_fallback", lambda *a: (_ for _ in ()).throw(RuntimeError("outage")))
    items, notes = cart_composer.revalidate(candidates, ["우유"], False)
    assert items == [] and notes
