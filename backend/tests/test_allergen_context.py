import pytest

from app.extractor import extract
from app.rule_engine import judge
from app.allergen_context import contexts


@pytest.fixture(autouse=True)
def rag_mode(monkeypatch):
    monkeypatch.setenv("KAC_ALLERGY_PROVIDER", "rag")


@pytest.mark.parametrize("text", ["정보 없음", "원재료 정보 없음", "N/A", "상세페이지 참조", "storage instructions only", "직사광선을 피해 보관하십시오", "rice, N/A"])
def test_missing_ingredients_cannot_pass(text):
    p = {"ingredients": text, "allergy_label": "", **extract(text, "")}
    assert not p["verified"]
    assert judge(p, ["우유"], generate=False)["status"] == "UNVERIFIED"


@pytest.mark.parametrize("text", ["rice, starch", "rice, modified starch"])
def test_english_starch_review(text):
    result = judge({"ingredients": text}, ["밀"], generate=False)
    assert result["status"] == "UNVERIFIED"
    assert any(e["id"] == "E007" for e in result["rag"]["exceptions"])


@pytest.mark.parametrize("text", ["milk-free, milk", "우유 함유 없음, 우유", "without milk, milk powder"])
def test_positive_occurrence_survives_negative(text):
    assert judge({"ingredients": text}, ["우유"], generate=False)["status"] == "BLOCK"


@pytest.mark.parametrize("label", ["May contain milk", "Made in a facility that processes milk"])
def test_english_cross_contact(label):
    p = {"ingredients": "rice", "allergy_label": label, **extract("rice", label)}
    assert p["contains"] == [] and p["cross"] == ["우유"]
    assert judge(p, ["우유"], generate=False)["status"] == "PASS"
    assert judge(p, ["우유"], True, generate=False)["status"] == "BLOCK"


@pytest.mark.parametrize("text", ["without milk", "no added milk", "milk is not contained", "우유 함유 아님", "우유는 함유하지 않음", "milk flavor", "사과향"])
def test_uncertain_context_not_containment(text):
    avoid = "사과" if text == "사과향" else "우유"
    assert avoid not in extract(text, "")["contains"]
    assert judge({"ingredients": text}, [avoid], generate=False)["status"] == "UNVERIFIED"


def test_offsets_and_original_quotes():
    text = "milk-free, milk"
    pieces = contexts(text, "ingredients")
    assert [p["kind"] for p in pieces] == ["negative", "contains"]
    assert all(text[p["start"]:p["end"]] == p["quote"] for p in pieces)


def test_invalid_ingredients_does_not_clear_declared_allergen():
    assert judge({"ingredients": "정보 없음", "allergy_label": "우유 함유"}, ["우유"], generate=False)["status"] == "BLOCK"


def test_mixed_cross_list_and_trailing_page_text():
    p = extract("rice", "May contain milk, wheat")
    assert p["contains"] == [] and set(p["cross"]) == {"우유", "밀"}
    assert extract("rice", "해당 없음 상품선택 우유")["contains"] == []
    assert "우유" in extract("프리미엄 우유", "")["contains"]
