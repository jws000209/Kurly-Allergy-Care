"""기획서의 검증 테스트 항목: 원재료 추출, 19종 매칭, 다중 프로필 합집합,
예산/개수 제약, 수정 후 재검증, 정보 누락 상품 제외."""
import os
import tempfile
from pathlib import Path

os.environ["KAC_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "test.sqlite3")
os.environ["KAC_SEED_PATH"] = str(Path(__file__).parent / "mock_products.json")  # 흐름 테스트용 고정 상품
for key in ("LLM_MODEL", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY", "GROQ_API_KEY"):
    os.environ[key] = ""  # 테스트는 규칙 파서로 고정 (.env의 키도 무시)

from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.allergens import ALLERGEN_NAMES  # noqa: E402
from app.extractor import extract  # noqa: E402
from app.intent import rule_parse  # noqa: E402
from app.main import app  # noqa: E402
from app.rule_engine import BLOCK, PASS, UNVERIFIED, combine_profiles, judge  # noqa: E402


def test_19_allergens():
    assert len(ALLERGEN_NAMES) == 19


def test_extract_ingredients():
    assert extract("밀가루(밀:국산), 버터(우유), 설탕, 달걀", "")["contains"] == ["밀", "알류", "우유"]
    assert extract("메밀가루, 정제소금", "")["contains"] == ["메밀"]            # '메밀'은 밀이 아니다
    assert extract("코코넛밀크, 땅콩버터", "")["contains"] == ["땅콩"]          # 우유·밀 오탐 없음
    assert extract("꽃게, 무, 된장(대두), 바지락", "")["contains"] == ["게", "대두", "조개류"]
    assert extract("망고, 무수아황산(산화방지제)", "")["contains"] == ["아황산류"]
    assert extract("쌀, 함께 볶은 채소", "")["contains"] == []                  # '함께'의 '게' 오탐 없음


def test_extract_kurly_label():
    label = "- 밀, 우유, 대두 함유\n* 계란, 메밀, 땅콩, 게 혼입 가능"
    result = extract("", label)
    assert result["contains"] == ["대두", "밀", "우유"]
    assert result["cross"] == ["게", "땅콩", "메밀", "알류"]


def test_extract_one_line_labels():
    """크롤링된 컬리 표시문은 한 줄로 이어져 있다. 함유 성분을 혼입으로 잘못 분류하면 안 된다."""
    label = ("- 우유, 대두, 밀 함유 - 이 제품은 알류, 메밀, 땅콩을 사용한 제품과 같은 제조시설에서 제조하고 있습니다. "
             "소비기한(또는 유통기한)정보 수령일 포함 최소 4일")
    result = extract("", label)
    assert result["contains"] == ["대두", "밀", "우유"] and result["cross"] == ["땅콩", "메밀", "알류"]

    label = "알류,우유,메밀 혼입 가능성 있음 대두,밀,조개류 함유"
    result = extract("", label)
    assert result["contains"] == ["대두", "밀", "조개류"] and result["cross"] == ["메밀", "알류", "우유"]

    label = "이 제품은 메밀, 땅콩을 사용한 제품과 동일 시설에서 제조하고 있습니다. 우유,대두,계란 함유"
    assert extract("", label)["contains"] == ["대두", "알류", "우유"]

    # 근거가 없는 안내 문구는 판정하지 않는다
    assert extract("", "옵션별 상이하여 하단 상세페이지 참고 부탁 드립니다.")["verified"] is False
    assert extract("", "- 해당사항 없음")["verified"] is True


def test_kurly_seed_file():
    """백엔드에 들어가는 실제 컬리 상품 시드가 비어 있지 않고 형식이 맞는지."""
    import json
    seed = json.loads((Path(__file__).parent.parent / "data" / "kurly_products.json").read_text(encoding="utf-8"))
    assert len(seed) >= 1000
    assert all(p["url"].startswith("https://www.kurly.com/goods/") and p["source"] == "kurly" for p in seed)
    assert not any("상품선택" in p["allergy_label"] for p in seed)


def test_score_composite_category():
    """'치킨/피자/핫도그/만두'처럼 묶인 소분류만 겹치는 상품은 '피자'를 콕 집은 요청에 강하게 걸리면 안 된다."""
    from app.cart_builder import STRONG_MATCH, _score
    category = "간편식/밀키트/샐러드>치킨/피자/핫도그/만두"
    assert _score({"name": "[비비고] 청양 고기만두", "category": category}, ["피자"]) < STRONG_MATCH
    assert _score({"name": "[고메] 콤비네이션 피자", "category": category}, ["피자"]) >= STRONG_MATCH
    assert _score({"name": "[농심] 신라면", "category": "면/양념/오일>라면"}, ["라면"]) >= STRONG_MATCH
    # '짜장' 요청: 같은 분류 '짜장/짬뽕/파스타/면류'의 칼국수·우동은 짜장이 아니다 (10/07 실제 화면에서 칼국수가 1번)
    noodle = "간편식/밀키트/샐러드>짜장/짬뽕/파스타/면류"
    assert _score({"name": "[서촌 영화루] 짜장면 2인분", "category": noodle}, ["짜장"]) >= STRONG_MATCH
    assert _score({"name": "[전주 베테랑] 칼국수", "category": noodle}, ["짜장"]) == 0
    assert _score({"name": "[정호영셰프의 우동카덴] 오리지널 우동", "category": noodle}, ["짜장"]) == 0
    assert _score({"name": "[동원] 딤섬 부추창펀 (390gX2봉)", "category": category}, ["치킨"]) == 0  # '2봉'은 닭봉 아님


def test_rule_engine_states():
    safe = {"verified": True, "contains": ["대두"], "cross": ["우유"]}
    assert judge(safe, ["우유"])["status"] == PASS
    assert judge(safe, ["우유"], strict=True)["status"] == BLOCK
    assert judge(safe, ["대두"])["status"] == BLOCK
    assert judge({"verified": False}, ["우유"])["status"] == UNVERIFIED


def test_profile_union():
    members = [{"name": "첫째", "allergens": ["우유", "알류"], "strict": False},
               {"name": "아빠", "allergens": ["땅콩"], "strict": True}]
    combined = combine_profiles(members, ["밀"])
    assert combined["avoid"] == ["땅콩", "밀", "알류", "우유"]
    assert combined["strict"] is True


def test_rule_parser():
    intent = rule_parse("첫째 우유·달걀 없이 간식 2만원 이내 5개", ["첫째", "아빠"], [])
    assert intent["action"] == "create_cart"
    assert intent["target_profiles"] == ["첫째"]
    assert intent["excluded_allergens"] == ["우유", "알류"]
    assert (intent["category_or_purpose"], intent["budget"], intent["item_count"]) == ("간식", 20000, 5)

    assert rule_parse("아이에게 줄 간식 추천해줘", [], [])["excluded_allergens"] == []
    cart = [{"name": "쌀과자"}]
    assert rule_parse("2번 상품 빼줘", [], cart)["action"] == "remove_item"
    replace = rule_parse("과자 대신 요거트", [], cart)
    assert (replace["action"], replace["from_keyword"], replace["to_keyword"]) == ("replace_item", "과자", "요거트")
    assert rule_parse("더 저렴하게", [], cart)["action"] == "cheaper"


def test_chat_flow():
    with TestClient(app) as client:
        user = client.post("/api/login", json={"name": "테스트"}).json()
        first = client.post(f"/api/users/{user['id']}/members",
                            json={"name": "첫째", "allergens": ["우유", "알류"]}).json()
        dad = client.post(f"/api/users/{user['id']}/members", json={"name": "아빠", "allergens": ["땅콩"]}).json()

        def say(message, members):
            return client.post("/api/chat", json={"user_id": user["id"], "thread_id": "t1",
                                                  "message": message, "selected_member_ids": members}).json()

        def assert_safe(cart, avoid):
            for item in cart:
                product = db.get_product(item["id"])
                assert product["verified"] and not set(product["contains"]) & avoid, item["name"]

        # 생성: 예산·개수 제약 + 정보 누락 상품 제외
        result = say("간식 2만원 이내 5개", [first["id"]])
        cart = result["cart"]
        assert len(cart) == 5 and sum(i["price"] for i in cart) <= 20000
        assert_safe(cart, {"우유", "알류"})

        # 수정: 빼기 → 교체 → 더 저렴하게, 매번 재검증
        removed = cart[1]["id"]
        cart = say("2번 상품 빼줘", [first["id"]])["cart"]
        assert len(cart) == 4 and removed not in [i["id"] for i in cart]

        cart = say("과자 대신 요거트", [first["id"]])["cart"]
        assert not any("과자" in i["category"] for i in cart)
        assert any("요거트" in i["category"] for i in cart)
        assert_safe(cart, {"우유", "알류"})

        before = sum(i["price"] for i in cart)
        cart = say("더 저렴하게", [first["id"]])["cart"]
        assert sum(i["price"] for i in cart) <= before
        assert_safe(cart, {"우유", "알류"})

        # 프로필을 추가로 고르면 합집합으로 재검증
        result = say("땅콩 없이 간식 3개", [first["id"], dad["id"]])
        assert result["constraints"]["avoid"] == ["땅콩", "알류", "우유"]
        assert_safe(result["cart"], {"우유", "알류", "땅콩"})

        # 예산이 부족해도 알레르기 조건은 유지하고 조건 변경을 요청
        result = say("간식 3천원 이내 5개", [first["id"]])
        assert sum(i["price"] for i in result["cart"]) <= 3000
        assert "예산" in result["reply"]
        assert_safe(result["cart"], {"우유", "알류"})

        # 프로필 없이 요청하면 장바구니를 만들지 않고 되묻는다
        result = client.post("/api/chat", json={"user_id": user["id"], "thread_id": "t2",
                                                "message": "간식 5개 담아줘", "selected_member_ids": []}).json()
        assert result["cart"] == [] and "프로필" in result["reply"]


def test_check_page_product():
    with TestClient(app) as client:
        user = client.post("/api/login", json={"name": "테스트2"}).json()
        member = client.post(f"/api/users/{user['id']}/members", json={"name": "딸", "allergens": ["우유"]}).json()
        product = {"id": "1001371570", "name": "[일동후디스] 아이얌 비스킷", "price": 4500,
                   "allergy_label": "- 밀, 우유, 대두 함유\n* 계란, 메밀 혼입 가능",
                   "url": "https://www.kurly.com/goods/1001371570"}
        result = client.post("/api/check", json={"user_id": user["id"], "member_ids": [member["id"]],
                                                 "product": product}).json()
        assert result["status"] == BLOCK and result["matched"] == ["우유"]
        assert db.get_product("1001371570")["source"] == "kurly-page"

        blank = {**product, "id": "999", "allergy_label": ""}
        result = client.post("/api/check", json={"user_id": user["id"], "member_ids": [member["id"]],
                                                 "product": blank, "save": False}).json()
        assert result["status"] == UNVERIFIED


def test_profile_aliases_and_resolution():
    from app import profiles

    family = [
        {"id": 1, "name": "첫째 아들", "relation": "아들", "order": "첫째", "aliases": ["민준"]},
        {"id": 2, "name": "아빠", "relation": "아빠", "order": "", "aliases": []},
        {"id": 3, "name": "둘째 딸", "relation": "딸", "order": "둘째", "aliases": []},
    ]

    def names(mentions):
        r = profiles.resolve(mentions, family)
        return ([m["name"] for _, m, _ in r["matched"]], [a for a, _ in r["ambiguous"]], r["unknown"])

    assert {"첫째", "아들", "큰애", "장남", "큰아들"} <= set(profiles.auto_aliases("첫째 아들", "아들", "첫째"))
    assert names(["첫째"]) == (["첫째 아들"], [], [])          # 이름 일부
    assert names(["큰애랑"]) == (["첫째 아들"], [], [])        # 순서 호칭 + 조사
    assert names(["민준이"]) == (["첫째 아들"], [], [])        # 직접 넣은 별칭
    assert names(["남편"]) == (["아빠"], [], [])               # 관계 동의어
    assert names(["아이"]) == ([], ["아이"], [])               # 아이가 둘이면 애매 → 되묻기
    assert names(["아이들"]) == (["첫째 아들", "둘째 딸"], [], [])
    assert names(["엄마"]) == ([], [], ["엄마"])               # 없는 사람 → 되묻기


def test_chat_asks_for_unknown_or_ambiguous_person():
    with TestClient(app) as client:
        user = client.post("/api/login", json={"name": "별칭테스트"}).json()
        uid = user["id"]
        son = client.post(f"/api/users/{uid}/members", json={
            "name": "첫째 아들", "allergens": ["우유"], "relation": "아들", "order": "첫째", "aliases": ["민준"]}).json()
        dad = client.post(f"/api/users/{uid}/members", json={"name": "아빠", "allergens": ["땅콩"], "relation": "아빠"}).json()
        assert "큰애" in son["auto_aliases"] and son["aliases"] == ["민준"]

        def say(message, thread):
            return client.post("/api/chat", json={"user_id": uid, "thread_id": thread, "message": message,
                                                  "selected_member_ids": []}).json()

        # 별칭으로 연결하면 이해한 내용을 먼저 알리고, 두 사람의 알레르겐 합집합으로 검증
        result = say("큰애랑 남편 간식 3개", "t1")
        assert result["constraints"]["avoid"] == ["땅콩", "우유"]
        assert "'큰애' → '첫째 아들'" in result["constraints"]["understood"]

        # 프로필에 없는 사람(엄마)이 끼면 추천하지 않고 되묻는다
        result = say("엄마랑 아빠 밀키트 2개", "t2")
        assert result["cart"] == [] and "'엄마'에 해당하는 가족 프로필이 없어요" in result["reply"]

        # 아이가 둘이면 '아이'는 애매 → 되묻기
        client.post(f"/api/users/{uid}/members", json={"name": "둘째 딸", "allergens": ["밀"], "relation": "딸", "order": "둘째"})
        result = say("아이 간식 3개", "t3")
        assert result["cart"] == [] and "여러 개" in result["reply"]

        # 잘못된 관계 값은 거부
        bad = client.post(f"/api/users/{uid}/members", json={"name": "x", "relation": "외계인"})
        assert bad.status_code == 422
        assert dad["relation"] == "아빠"


def test_groups_missing_type_and_situation_lifetime():
    with TestClient(app) as client:
        user = client.post("/api/login", json={"name": "묶음상황"}).json()
        kid = client.post(f"/api/users/{user['id']}/members", json={"name": "첫째", "allergens": ["우유"]}).json()

        def say(message):
            return client.post("/api/chat", json={"user_id": user["id"], "thread_id": "g1", "message": message,
                                                  "selected_member_ids": [kid["id"]]}).json()

        # 묶음 추천 + 없는 상품 유형 알리기 (시험용 상품에는 만두가 없다)
        result = say("만두랑 과자 추천해줘")
        assert "'만두'은(는) 지금 상품 DB에 없어서" in result["reply"]
        assert result["cart"] and all(item["group"] == "과자" for item in result["cart"])

        # 상황은 그 추천과 수정에만 적용되고, 새 추천 요청에서는 사라진다
        result = say("소풍 갈 때 먹을 간식 3개")
        assert result["constraints"]["situation"]["name"] == "소풍"
        assert "상황: 소풍" in result["reply"]
        result = say("2번 상품 빼줘")
        assert result["constraints"]["situation"]["name"] == "소풍"
        result = say("간식 2개")
        assert result["constraints"]["situation"] is None and "상황:" not in result["reply"]


def test_situation_presets():
    from app import situations

    picnic = situations.build("피크닉")
    assert picnic["name"] == "소풍" and "찌개" in picnic["avoid"]
    assert situations.build("저녁") is None                      # 식사 시간은 상황이 아님
    assert situations.build("등산", ["김밥"], [])["source"] == "LLM"  # 표에 없으면 LLM 제안 낱말
    assert situations.build(None) is None


def test_llm_values_must_come_from_this_sentence():
    """이전 대화를 LLM에 넘겨도 상황·예산·개수·사람은 이번 문장에 근거가 있을 때만 쓴다."""
    from app.intent import _ground, _history_text

    history = [{"user": "아빠랑 소풍 가려는데 점심 2만원 안에서 3개 추천해줘", "bot": "추천 상품 3개", "asked": False}]
    # LLM이 이전 대화에서 소풍·예산·개수·아빠를 끌어왔다고 가정
    leaked = {"action": "create_cart", "situation": "소풍", "situation_prefer": ["김밥"], "situation_avoid": ["찌개"],
              "budget": 20000, "item_count": 3, "target_profiles": ["아빠"], "excluded_allergens": []}
    cleaned = _ground(dict(leaked), "저녁 반찬 추천해줘", history)
    assert cleaned["situation"] is None and cleaned["situation_prefer"] == [] and cleaned["situation_avoid"] == []
    assert cleaned["budget"] is None and cleaned["item_count"] is None and cleaned["target_profiles"] == []

    # 이번 문장에 있으면 그대로 ('피크닉'은 소풍의 다른 말)
    kept = _ground(dict(leaked), "아빠랑 피크닉 점심 2만원 3개", history)
    assert kept["situation"] == "소풍" and kept["budget"] == 20000 and kept["item_count"] == 3
    assert kept["target_profiles"] == ["아빠"]

    # 챗봇이 바로 앞에서 되물었으면 그 요청 문장도 근거로 인정 (되물음에 답하는 중)
    asked = [{"user": "엄마랑 소풍 점심 3개", "bot": "'엄마' 프로필이 없어요", "asked": True}]
    answered = _ground(dict(leaked, target_profiles=["아빠"]), "아빠로 해줘", asked)
    assert answered["situation"] == "소풍" and answered["item_count"] == 3 and answered["target_profiles"] == ["아빠"]

    assert "챗봇(되물음): '엄마' 프로필이 없어요" in _history_text(asked)
    assert _history_text([]) == "없음"


def test_chat_keeps_short_history():
    from app import agent

    with TestClient(app) as client:
        user = client.post("/api/login", json={"name": "대화기록"}).json()
        kid = client.post(f"/api/users/{user['id']}/members", json={"name": "첫째", "allergens": ["우유"]}).json()
        for i in range(8):
            client.post("/api/chat", json={"user_id": user["id"], "thread_id": "h1", "message": f"간식 {i + 1}개",
                                           "selected_member_ids": [kid["id"]]})
        history = agent.agent.get_state({"configurable": {"thread_id": "h1"}}).values["history"]
        assert len(history) == agent.HISTORY_KEEP and history[-1]["user"] == "간식 8개"
        assert history[-1]["bot"] and not history[-1]["asked"]


def _fake_llm(monkeypatch, picks=None, fail=False):
    """LLM 선택(selector)만 가짜 응답으로 바꾼다. 조건 해석은 규칙 파서 그대로."""
    from app import intent as intent_parser
    from app import selector

    calls = []

    def fake_invoke(build, payload):
        calls.append(payload)
        if fail:
            raise RuntimeError("429")
        return selector.Picks(picks=[selector.Pick(no=n, why=f"이유{n}") for n in picks]), "fake:model"

    # 조건 해석은 키가 없어 규칙 파서로 가고(llm_models()가 비어 있음), 고르기만 가짜 LLM을 쓴다
    monkeypatch.setattr(intent_parser, "invoke_with_fallback", fake_invoke)
    monkeypatch.setattr(selector, "should_use", lambda situation=None, preferences=None: True)
    return calls


def _candidates(n=10):
    return [{"id": str(i), "name": f"상품{i}", "price": 1000 * i, "category": f"간편식>분류{i % 3}", "score": 5}
            for i in range(1, n + 1)]


def test_selector_picks_only_from_candidates(monkeypatch):
    from app import selector

    calls = _fake_llm(monkeypatch, picks=[7, 99, 7, 2])   # 후보 밖 번호(99)·중복(7)은 버린다
    items, notes, model = selector.select(_candidates(), 3, None, "소풍 점심", {"name": "소풍"}, [])
    assert [i["id"] for i in items][:2] == ["7", "2"] and len(items) == 3     # 모자란 1개는 점수 순으로 채움
    assert items[0]["why"] == "이유7" and model == "fake:model"
    assert "7. 상품7 / 7,000원 / 분류1" in calls[0]["candidates"]

    _fake_llm(monkeypatch, picks=[10, 9])                  # 예산을 넘으면 코드가 더 싼 후보로 바꾼다
    items, _, _ = selector.select(_candidates(), 2, 12000, "소풍", {"name": "소풍"}, [])
    assert sum(i["price"] for i in items) <= 12000

    _fake_llm(monkeypatch, fail=True)                       # 호출 실패 → None (점수 순으로)
    assert selector.select(_candidates(), 3, None, "소풍", {"name": "소풍"}, []) is None
    assert selector.select(_candidates(2), 3, None, "소풍", {"name": "소풍"}, []) is None   # 고를 게 없으면 안 부름


def test_chat_uses_llm_selector_for_new_recommendations(monkeypatch):
    """새 추천마다 LLM이 고르고(상황이 없어도), 고치는 요청('2번 빼줘')에는 부르지 않는다."""
    calls = _fake_llm(monkeypatch, picks=[1, 2])
    with TestClient(app) as client:
        user = client.post("/api/login", json={"name": "LLM선택"}).json()
        kid = client.post(f"/api/users/{user['id']}/members", json={"name": "첫째", "allergens": ["우유"]}).json()

        def say(message):
            return client.post("/api/chat", json={"user_id": user["id"], "thread_id": "p1", "message": message,
                                                  "selected_member_ids": [kid["id"]]}).json()

        result = say("간식 2개")                                    # 상황·취향이 없어도 LLM이 고름
        assert result["picked_by"] == "fake:model" and len(calls) == 1
        result = say("소풍 갈 때 먹을 간식 2개")
        assert result["picked_by"] == "fake:model" and len(calls) == 2
        assert all(item["status"] == "PASS" and item.get("why") for item in result["cart"])
        assert calls[1]["situation"] == "소풍" and "소풍" in calls[1]["request"]
        result = say("2번 빼줘")                                    # 고치는 요청은 지금 방식 그대로
        assert len(calls) == 2 and len(result["cart"]) == 1


def test_again_shows_new_products_first(monkeypatch):
    """같은 대화에서 같은 요청을 다시 하거나 '다른 거 추천해줘'라고 하면 앞에서 보여 준 상품은 뒤로 돌린다."""
    from app import intent as intent_parser
    monkeypatch.setattr(intent_parser, "llm_models", lambda: [])   # 점수 순 고르기로 확인
    with TestClient(app) as client:
        user = client.post("/api/login", json={"name": "다시추천"}).json()
        kid = client.post(f"/api/users/{user['id']}/members", json={"name": "첫째", "allergens": ["우유"]}).json()

        def say(message):
            return client.post("/api/chat", json={"user_id": user["id"], "thread_id": "again1", "message": message,
                                                  "selected_member_ids": [kid["id"]]}).json()

        first = {i["id"] for i in say("간식 2개")["cart"]}
        second = say("다른 거 추천해줘")
        assert len(second["cart"]) == 2 and not first & {i["id"] for i in second["cart"]}
        assert second["constraints"]["terms"] == ["간식"]                 # 직전 조건 그대로
        third = {i["id"] for i in say("간식 2개")["cart"]}                # 같은 요청을 다시 해도 새 상품부터
        assert not third & (first | {i["id"] for i in second["cart"]})


def test_cross_only_label_is_unverified():
    """'같은 제조시설' 문구만 있고 함유 표시가 없으면 판정하지 않는다 (라면: 함유 성분은 뒷면 이미지에만 있음)."""
    label = "메밀, 땅콩, 고등어, 게, 새우를 사용한 제품과 같은 제조시설에서 제조하고 있습니다."
    result = extract("", label)
    assert result["contains"] == [] and result["cross"] == ["게", "고등어", "땅콩", "메밀", "새우"]
    assert result["verified"] is False
    verdict = judge({**result, "id": "x"}, ["밀"])
    assert verdict["status"] == UNVERIFIED and "같은 제조시설" in verdict["reason"]
    # 함유 표시가 함께 있거나 원재료명이 있으면 판정한다
    assert extract("", "밀, 대두 함유. " + label)["verified"] is True
    assert extract("소맥분(밀), 정제염", label)["verified"] is True


def test_init_db_syncs_products_with_seed(tmp_path, monkeypatch):
    """새로 수집한 시드로 바꾸면: 추가·갱신·시드에서 빠진 컬리 상품 삭제. 프로필과 시드에 없는 페이지 저장 상품은 유지."""
    import json

    def product(pid, price, source="kurly"):
        return {"id": pid, "name": f"상품{pid}", "price": price, "category": "간편식>라면", "ingredients": "",
                "allergy_label": "밀, 대두 함유", "url": f"https://www.kurly.com/goods/{pid}", "image": "", "source": source}

    seed = tmp_path / "seed.json"
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "sync.sqlite3")
    monkeypatch.setattr(db, "SEED_PATH", seed)

    seed.write_text(json.dumps([product("a", 1000), product("b", 2000)]), encoding="utf-8")
    db.init_db()
    user = db.login("동기화")
    db.create_member(user["id"], "첫째", ["밀"], False)
    db.upsert_product(product("page", None, "kurly-page"))   # 사용자가 컬리 페이지에서 저장한 상품

    seed.write_text(json.dumps([product("a", 1500), product("c", 3000)]), encoding="utf-8")
    db.init_db()
    products = {p["id"]: p for p in db.list_products()}
    assert products["a"]["price"] == 1500          # 갱신
    assert "b" not in products                     # 시드에서 빠짐 → 삭제
    assert "c" in products and "page" in products  # 추가 / 페이지 저장 상품 유지
    assert db.login("동기화")["id"] == user["id"]   # 계정·프로필 유지
    assert [m["name"] for m in db.list_members(user["id"])] == ["첫째"]


def test_label_typos_and_particles():
    """컬리 표시문에서 실제로 본 오탈자·조사 때문에 함유 성분을 놓치지 않는다 (10/07 v2 데이터)."""
    assert extract("", "돼지고기, 쇠고기, 대우, 밀, 우윰 함유")["contains"] == ["대두", "돼지고기", "밀", "쇠고기", "우유"]
    assert "우유" in extract("", "-밀, 대두, 유유, 땅콩, 달걀 함유")["contains"]
    assert "아황산류" in extract("", "- 닭고기, 대두, 이산화항 함유")["contains"]
    assert extract("", "대두, 달고기, 계란 함유")["contains"] == ["닭고기", "대두", "알류"]
    cross = extract("", "밀 함유. 이 제품은 고등어, 개, 세우, 메일, 오장어, 조개류(굴, 전복, 홍함 포함)를 사용한 제품과 같은 제조시설")["cross"]
    assert {"게", "새우", "메밀", "오징어", "조개류", "고등어"} <= set(cross)
    assert "게" in extract("", "밀 함유. 본 제품은 게를 사용한 제품과 같은 시설에서 제조")["cross"]   # 조사가 붙은 한 글자 성분
    # 다른 낱말은 바꾸지 않는다
    assert extract("", "유유제약 비타민. 해당 사항 없음")["contains"] == []
    assert extract("", "1개, 2개 묶음. 해당 사항 없음")["contains"] == []
    # 주의 문구의 '수유부'는 대두(유부)가 아니다 → 판정 근거 없음
    assert extract("", "임산부 및 수유부는 섭취를 피하십시오.")["verified"] is False


def test_non_allergen_contains_statement_and_reviewed_blank():
    """19종이 아닌 성분의 함유 표시('양고기 함유')도 판정 근거다. 팀 검토로 원물 판단한 빈 칸은 사유를 밝힌다."""
    from app.extractor import NO_LABEL_REVIEWED

    lamb = extract("", "- 양고기 함유 - 이 제품은 난류, 우유, 땅콩을 사용한 제품과 같은 제조시설에서 제조")
    assert lamb["verified"] is True and lamb["contains"] == [] and lamb["cross"] == ["땅콩", "알류", "우유"]
    assert extract("", "- 산양유 함유")["contains"] == ["우유"]
    # 괄호 안의 '포함'은 함유 표시가 아니다 (제조시설 문구만 있는 상품은 계속 판정 불가)
    assert extract("", "이 제품은 조개류(굴, 전복, 홍합 포함)를 사용한 제품과 같은 제조시설")["verified"] is False

    reviewed = {**extract("", NO_LABEL_REVIEWED), "allergy_label": NO_LABEL_REVIEWED}
    assert reviewed["verified"] is True and reviewed["contains"] == []
    verdict = judge(reviewed, ["우유"])
    assert verdict["status"] == PASS and "라벨 확인 아님" in verdict["reason"]
    # '함유'가 들어간 주의 문구는 알레르기 함유 표시가 아니다
    assert extract("", "-당알코올 함유 제품으로 과량 섭취 시 설사를 일으킬 수 있습니다.")["verified"] is False
    assert extract("", "- 페닐알라닌 함유 * 이 제품은 우유, 밀을 사용한 제품과 같은 제조시설에서 제조")["verified"] is False


def test_min_purchase_quantity_counts_in_price(tmp_path, monkeypatch):
    """컬리 최소 구매 수량(예: 2봉)만큼 장바구니에 담기므로 가격·예산은 곱한 금액으로 계산한다."""
    import json
    from app import cart_builder

    seed = tmp_path / "seed.json"
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "minea.sqlite3")
    monkeypatch.setattr(db, "SEED_PATH", seed)
    base = {"category": "간편식>만두", "ingredients": "", "allergy_label": "돼지고기, 대두, 밀 함유", "image": "", "source": "kurly"}
    seed.write_text(json.dumps([{**base, "id": "m2", "name": "김치만두", "price": 5980, "min_ea": 2,
                                  "url": "https://www.kurly.com/goods/m2"},
                                 {**base, "id": "m1", "name": "고기만두", "price": 7000,
                                  "url": "https://www.kurly.com/goods/m1"}]), encoding="utf-8")
    db.init_db()
    items, _ = cart_builder.search(["만두"], ["우유"], False)
    two = next(i for i in items if i["id"] == "m2")
    assert two["price"] == 11960 and two["unit_price"] == 5980 and two["min_ea"] == 2
    picked, notes = cart_builder.build(items, 2, 15000)   # 5,980원짜리도 실제로는 11,960원이라 둘 다는 못 담음
    assert cart_builder.total(picked) <= 15000


def test_live_min_ea_from_kurly_page(tmp_path, monkeypatch):
    """담을 때 컬리 상품 페이지에서 읽은 최소 구매 수량(데이터엔 1)을 반영하고, 서버를 다시 켜도(시드 동기화) 유지한다."""
    import json

    seed = tmp_path / "seed.json"
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "live.sqlite3")
    monkeypatch.setattr(db, "SEED_PATH", seed)
    monkeypatch.setattr(db, "LIVE_MIN_EA_PATH", tmp_path / "min_ea_live.json")
    seed.write_text(json.dumps([{"id": "k1", "name": "칼국수", "price": 5900, "category": "간편식>면류", "ingredients": "",
                                 "allergy_label": "밀 함유", "image": "", "source": "kurly",
                                 "url": "https://www.kurly.com/goods/k1"}]), encoding="utf-8")
    db.init_db()
    client = TestClient(app)
    assert client.post("/api/products/k1/min-ea", json={"min_ea": 2}).json() == {"updated": True}
    assert client.post("/api/products/k1/min-ea", json={"min_ea": 2}).json() == {"updated": False}
    db.init_db()
    assert db.get_product("k1")["min_ea"] == 2


def test_ranking_by_reviews_then_sales_rank():
    """같은 관련도 안에서는 후기 수 → 판매량 순위로 추천하고 가격은 순서에 쓰지 않는다. 소분류가 맞는 상품이 이름만 맞는 상품보다 앞선다."""
    from app.cart_builder import _popularity, _score

    items = [{"id": "a", "review_count": 100, "sales_rank": 5, "price": 1000},
             {"id": "b", "review_count": 900, "sales_rank": 50, "price": 9000},
             {"id": "c", "review_count": 900, "sales_rank": 3, "price": 9900},
             {"id": "d", "review_count": None, "sales_rank": 1, "price": 500}]
    assert [i["id"] for i in sorted(items, key=_popularity)] == ["c", "b", "a", "d"]
    fruit = {"name": "샤인머스캣 1kg", "category": "과일/견과/쌀>국산과일"}
    sauce = {"name": "[참소스] 과일품은 참소스", "category": "면/양념/오일>식초/소스/드레싱"}
    assert _score(fruit, ["과일"]) > _score(sauce, ["과일"])


def test_group_and_plural_person_words():
    """'자식들'·'딸들'·'부모님'·'가족'처럼 묶어 부르면 해당 프로필을 모두 대상으로 한다 (되묻지 않음)."""
    from app import profiles

    members = [{"id": 1, "name": "둘째 아들", "relation": "아들", "order": "둘째", "aliases": []},
               {"id": 2, "name": "엄마", "relation": "엄마", "order": "", "aliases": []},
               {"id": 3, "name": "아빠", "relation": "아빠", "order": "", "aliases": []},
               {"id": 4, "name": "첫째 딸", "relation": "딸", "order": "첫째", "aliases": []},
               {"id": 5, "name": "셋째 딸", "relation": "딸", "order": "셋째", "aliases": []}]

    def names(mentions):
        r = profiles.resolve(mentions, members)
        return sorted(m["name"] for _, m, _ in r["matched"]), r["ambiguous"], r["unknown"]

    assert names(["자식들"]) == (["둘째 아들", "셋째 딸", "첫째 딸"], [], [])
    assert names(["아들", "딸들"]) == (["둘째 아들", "셋째 딸", "첫째 딸"], [], [])
    assert names(["딸들이랑"]) == (["셋째 딸", "첫째 딸"], [], [])
    assert names(["부모님"]) == (["아빠", "엄마"], [], [])
    assert len(names(["가족"])[0]) == 5
    assert names(["딸"])[1]                       # 한 명을 가리키는데 둘이면 여전히 되묻는다
    message = profiles.understood_message(profiles.resolve(["딸들"], members))
    assert message == "이렇게 이해했어요: '딸들' → '첫째 딸'·'셋째 딸'"


def test_generic_words_are_not_product_types():
    from app.agent import _real_terms
    from app import situations

    assert _real_terms(["음식", "간식", "먹을 거리"]) == ["간식"]
    assert situations.find_preset("아들이 생일인데 먹을 음식") == "생일"


def test_strong_matches_are_not_padded_with_weak_ones(tmp_path, monkeypatch):
    """'피자'를 원했는데 피자가 하나뿐이면, 같은 묶음 분류의 만두로 채우지 않고 부족하다고 알린다. '간식' 같은 목적어는 그대로."""
    import json
    from app import cart_builder

    seed = tmp_path / "seed.json"
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "pad.sqlite3")
    monkeypatch.setattr(db, "SEED_PATH", seed)
    cat = "간편식/밀키트/샐러드>치킨/피자/핫도그/만두"
    rows = [{"id": "p", "name": "화덕피자 도우", "price": 4600}] +            [{"id": f"d{i}", "name": f"고기만두 {i}", "price": 9000} for i in range(4)]
    seed.write_text(json.dumps([{**r, "category": cat, "ingredients": "", "allergy_label": "대두 함유", "image": "",
                                 "source": "kurly", "url": f"https://www.kurly.com/goods/{r['id']}"} for r in rows]),
                    encoding="utf-8")
    db.init_db()
    cands, _ = cart_builder.search(["피자"], ["우유"], False)
    items, notes = cart_builder.build(cands, 5, None)
    assert [i["id"] for i in items] == ["p"] and "1개뿐" in notes[0]
    cands, _ = cart_builder.search(["만두"], ["우유"], False)
    assert len(cands) == 4 and all(c["id"].startswith("d") for c in cands)


def test_relevance_rules_from_real_data_audit():
    """10/07 실제 데이터로 55개 요청을 점검하며 찾은 오탐을 막는 규칙."""
    from app.cart_builder import STRONG_MATCH, _score

    def s(name, category, term):
        return _score({"name": name, "category": category}, [term])

    combo = "간편식/밀키트/샐러드>치킨/피자/핫도그/만두"
    assert s("[비비고] 왕교자 455gx2", combo, "피자") == 0                     # 교자는 만두 → 피자 아님
    assert s("[빙그레] 모짜렐라 피자치즈 1kg", "유제품>가공치즈", "피자") < STRONG_MATCH   # 꾸미는 말
    assert s("[쉐푸드] 참치마요 삼각김밥 (냉동/3개입)", "간편식/밀키트/샐러드>도시락/밥류", "김밥") >= STRONG_MATCH
    assert s("[하선정] 김밥 단무지 400g", "국/반찬/메인요리>밑반찬", "김밥") < STRONG_MATCH
    assert s("[Kurly's] 국산콩 두부 300g", "국/반찬/메인요리>두부/어묵/부침개", "국") == 0   # 한 글자는 낱말 끝만
    assert s("[KF365] 한돈 삼겹살", "정육/가공육/달걀>국내산 돼지고기", "국") == 0
    assert s("[마마리] 들깨 미역국", "국/반찬/메인요리>국/탕/찌개", "국") >= STRONG_MATCH
    assert s("[돈시몬] 착즙 토마토주스", "생수/음료>과일/야채음료", "주스") == 3           # 분류 이름이 다른 말
    assert s("[솔리몬] 레몬즙 280ml", "면/양념/오일>식초/소스/드레싱", "주스") == 0
    assert s("저탄소 사과 1.3kg", "과일/견과/쌀>제철과일", "사과") > s("[남양] 마시는 불가리스 사과", "유제품>요거트/생크림", "사과")
    assert s("[서울우유] 아침에 버터 200g", "유제품>버터", "아침") == 0                   # 목적어는 이름에서 찾지 않음


def test_shortage_note_goes_below_the_list(tmp_path, monkeypatch):
    """개수 부족 안내는 답변 글이 아니라 추천 목록 아래(notes_after)에 붙고, 되묻는 문장은 없다."""
    import json

    seed = tmp_path / "seed.json"
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "after.sqlite3")
    monkeypatch.setattr(db, "SEED_PATH", seed)
    rows = [{"id": f"u{i}", "name": f"사누끼 우동면 {i}", "price": 5000} for i in range(3)]
    seed.write_text(json.dumps([{**r, "category": "면/양념/오일>파스타/면류/조리용 떡", "ingredients": "",
                                 "allergy_label": "밀 함유", "image": "", "source": "kurly",
                                 "url": f"https://www.kurly.com/goods/{r['id']}"} for r in rows]), encoding="utf-8")
    with TestClient(app) as client:
        user = client.post("/api/login", json={"name": "부족"}).json()
        kid = client.post(f"/api/users/{user['id']}/members", json={"name": "첫째", "allergens": ["우유"]}).json()
        result = client.post("/api/chat", json={"user_id": user["id"], "thread_id": "a1", "message": "우동 5개",
                                                "selected_member_ids": [kid["id"]]}).json()
    assert len(result["cart"]) == 3
    assert result["notes_after"] == ["요청한 종류 중 알레르기 조건을 통과한 상품이 3개뿐이라 5개를 채우지 못했어요."]
    assert "채우지 못했어요" not in result["reply"] and "보시겠어요" not in result["reply"]


def test_budget_modes():
    """예산을 상한·하한·안팎·범위·개당으로 구분하고, 조건을 지키면서 인기 순서를 최대한 따른다."""
    from app import cart_builder
    from app.intent import _budget_detail

    assert _budget_detail("밀키트 1개 2만원 넘게")["budget_mode"] == "min"
    assert _budget_detail("밀키트 1개 2만원에 가깝게")["budget_mode"] == "around"
    assert _budget_detail("간식 1~2만원 3개")["budget_min"] == 10000
    assert _budget_detail("만원짜리 밀키트 3개") == {"budget": 10000, "budget_mode": None, "budget_min": None, "per_item": True}
    assert _budget_detail("2만원 선에서")["budget_mode"] == "max"

    # 인기순(앞에 있을수록 인기) 후보, 가격만 다름
    pool = [{"id": str(p), "price": p, "category": f"c{i % 3}", "score": 3} for i, p in
            enumerate([14900, 9000, 21000, 19500, 5500, 6000, 25000, 7000])]
    pick = lambda count, budget: [i["price"] for i in cart_builder.build(pool, count, budget)[0]]
    assert pick(1, {"amount": 20000, "mode": "min"}) == [21000]                   # 2만원 넘게
    assert 17000 <= pick(1, {"amount": 20000, "mode": "around"})[0] <= 23000      # 2만원 안팎 (±15%, 그 안에서는 인기순)
    assert pick(1, {"amount": 20000, "mode": "max"}) == [14900]                   # 2만원 이내 → 가장 인기 있는 것
    assert pick(3, {"amount": 10000, "mode": "max", "per_item": True}) == [9000, 5500, 6000]   # 개당 1만원 이하
    picked = pick(5, {"amount": 45000, "mode": "max"})        # 가장 싼 5개 합이 42,400원 → 4.5만원이면 5개 가능
    assert len(picked) == 5 and sum(picked) <= 45000            # 인기순으로 담다 넘친다고 개수를 줄이지 않음
    assert len(pick(5, {"amount": 30000, "mode": "max"})) == 4  # 3만원으로는 정말 4개가 최대
    items, notes = cart_builder.build(pool, 3, {"amount": 5000, "mode": "max"})
    assert items == [] and "가장 싼 상품: 5,500원" in notes[0]                     # 0개면 왜 없는지
    assert "보시겠어요" not in " ".join(notes)


def test_group_words_with_old_profiles_without_relation():
    """관계·순서 칸이 비어 있는 예전 프로필('둘째 아들', '첫째 딸')도 '자식들'·'부모님'·'큰애'로 찾는다 (10/07 실제 화면)."""
    from app.profiles import resolve
    members = [{"id": i, "name": n, "relation": "", "order": "", "aliases": []}
               for i, n in enumerate(["둘째 아들", "엄마", "아빠", "첫째 딸", "셋째 딸"])]
    names = lambda word: [m["name"] for _, m, _ in resolve([word], members)["matched"]]
    assert names("자식들") == ["둘째 아들", "첫째 딸", "셋째 딸"]
    assert names("딸들") == ["첫째 딸", "셋째 딸"]
    assert names("부모님") == ["엄마", "아빠"]
    assert names("큰애") == ["첫째 딸"]


def test_profile_named_like_product():
    """프로필 이름이 상품 이름과 같을 때('라면'): "라면이 먹을 피자"는 대상 라면, 종류 피자 (10/07 실제 화면: 라면 2·피자 2)."""
    from app.intent import _drop_person_words
    llm_like = {"target_profiles": ["라면"], "category_or_purpose": "라면", "keywords": ["피자"],
                "groups": [{"label": "라면", "keywords": ["라면"], "count": None},
                           {"label": "피자", "keywords": ["피자"], "count": None}]}
    fixed = _drop_person_words(llm_like, "라면이 먹을 피자")
    assert fixed["category_or_purpose"] == "피자" and fixed["keywords"] == [] and fixed["groups"] == []
    both = {"target_profiles": ["라면"], "category_or_purpose": "라면", "keywords": [], "groups": []}
    assert _drop_person_words(both, "라면이 먹을 라면")["category_or_purpose"] == "라면"   # 두 번 나오면 상품도 라면
    parsed = rule_parse("라면이 먹을 피자", ["라면"], [], ["라면"])
    assert parsed["target_profiles"] == ["라면"] and parsed["category_or_purpose"] == "피자" and not parsed["groups"]


def test_lookalike_words_do_not_match():
    """'콜라' 요청에 쇼콜라·콜라겐·콜라비는 콜라가 아니다 (10/07 실제 화면: 콜라 → 쇼콜라 4개)."""
    from app.cart_builder import _score
    assert _score({"name": "[파스키에] 팡오쇼콜라 (45g X 6개입)", "category": "베이커리>식빵/모닝빵/베이글"}, ["콜라"]) == 0
    assert _score({"name": "[니아르] 퓨어 콜라겐 28정", "category": "건강식품>체중관리"}, ["콜라"]) == 0
    assert _score({"name": "펩시 제로슈거 콜라 355mL", "category": "생수/음료>탄산/스포츠음료"}, ["콜라"]) >= 2
