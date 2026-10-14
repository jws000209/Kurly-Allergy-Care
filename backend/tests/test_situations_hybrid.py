import pytest
from app import situations, intent, cart_builder, db


@pytest.mark.parametrize("text,expected", [
    ("소풍 아니고 캠핑에서 라면", "캠핑"), ("다이어트 안 해. 치즈 추천", None),
    ("도시락 반찬 2개", None), ("식단 추천", None), ("생일 파티", "생일"),
    ("캠핑 안 가고 소풍", "소풍"),
])
def test_positive_situation_priority(text, expected):
    assert situations.find_preset(text) == expected


def test_wrong_situation_is_not_grounded_by_different_preset():
    data = {"situation": "캠핑", "situation_prefer": ["라면"], "situation_avoid": []}
    assert intent._ground(data, "아들 생일 간식 추천", [])["situation"] is None
    assert not situations.grounded("다이어트", "다이어트 안 해")
    assert situations.grounded("캠핑", "소풍 아니고 캠핑 갈 거야")


def test_hybrid_priorities_and_conflict_resolution():
    built = situations.build("소풍", ["과일"], ["라면", "과일", "탕"], ["컵라면"])
    assert built["source"] == "혼합" and built["filter_mode"] == "soft"
    assert "과일" in built["prefer"] and "과일" not in built["avoid"]
    assert "라면" not in built["avoid"] and "탕" not in built["avoid"]
    unknown = situations.build("등산", ["김밥"], ["김밥"])
    assert unknown["avoid"] == []


def test_situation_never_removes_explicit_item_or_candy():
    db.init_db()
    for pid, name, category, ingredients in [
        ("ramen", "컵라면", "간편식>라면", "쌀"),
        ("candy", "소풍용 사탕", "간식>과자", "설탕"),
        ("blocked", "우유 사탕", "간식>과자", "우유"),
    ]:
        db.upsert_product({"id": pid, "name": name, "price": 1000, "category": category, "ingredients": ingredients})
    situation = situations.build("소풍")
    ramen, stats = cart_builder.search(["컵라면"], ["우유"], False, situation=situation)
    assert "ramen" in {p["id"] for p in ramen} and stats["situation_excluded"] == 0
    candy, _ = cart_builder.search(["사탕"], ["우유"], False, situation=situation)
    assert "candy" in {p["id"] for p in candy} and "blocked" not in {p["id"] for p in candy}


def test_selector_receives_full_criteria(monkeypatch):
    from app import selector
    captured = []
    def fake(build, payload):
        captured.append(payload)
        return selector.Picks(picks=[selector.Pick(no=1, why="휴대하기 편함")]), "mock"
    monkeypatch.setattr(intent, "invoke_with_fallback", fake)
    candidates = [{"id": str(i), "name": "샌드위치", "price": 1000, "category": "간식", "score": 1} for i in range(3)]
    selector.select(candidates, 1, None, "소풍 간식", situations.build("소풍"), [])
    assert "샌드위치" in captured[0]["situation_criteria"]
    assert "prefer" in captured[0]["situation_criteria"] and "why" in captured[0]["situation_criteria"]


def test_personalization_preserves_soft_penalty(monkeypatch):
    from app import recommender
    monkeypatch.setattr(recommender, "score_products", lambda *a: {
        "normal": {"recommendation_score": 0}, "penalty": {"recommendation_score": 1}})
    candidates = [{"id": "penalty", "score": 3, "situation_disfavored": True}, {"id": "normal", "score": 3}]
    assert recommender.personalize(candidates, [], [], 1)[0]["id"] == "normal"
