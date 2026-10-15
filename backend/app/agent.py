"""LangGraph Agent: 대화 상태와 현재 장바구니를 유지하며 흐름을 제어한다.

parse_intent → resolve_conditions → (build_cart | edit_cart | answer | ask_profile) → revalidate → respond → remember

LLM은 parse_intent(조건 구조화), answer(일반 질문 응답), 그리고 새 추천마다
Rule Engine을 통과한 후보 안에서 고르기(selector)에 쓰인다.
장바구니에 담기는 상품은 항상 Rule Engine을 거치고, 수정 후에도 전체를 재검증한다.
"""
import functools
import re
import os
from typing import TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from . import cart_composer, db, intent as intent_parser, profiles, selector, situations
from .rule_engine import BLOCK, PASS, UNVERIFIED, combine_profiles

CART_ACTIONS = {"create_cart", "update_constraints"}
# 상품 종류를 정하지 않는 일반적인 말. 검색어로 쓰면 'DB에 없는 유형'으로 오해하므로 뺀다 (예: '먹을 음식 추천해줘').
GENERIC_TERMS = {"음식", "먹을거", "먹을것", "먹거리", "먹을거리", "식품", "요리", "메뉴", "상품", "제품", "물건",
                 "장보기", "장", "거", "것", "추천", "뭐", "아무거나"}


def _real_terms(words) -> list[str]:
    return list(dict.fromkeys(w for w in words if w and w.replace(" ", "") not in GENERIC_TERMS))
EDIT_ACTIONS = {"remove_item", "replace_item", "add_item", "cheaper"}
AGAIN_PATTERN = re.compile(r"다른\s*(거|걸|것|상품|종류)|다시\s*(추천|골라|찾아)|또\s*(추천|골라)|새로\s*(추천|골라)")

HELP_TEXT = ("가족 프로필의 알레르기 조건에 맞춰 상품을 추천해 드려요.\n"
             "예) \"첫째 간식 2만원 이내 5개\", \"요거트 추천해줘\", \"더 저렴하게\"\n"
             "원하는 상품의 '장바구니에 추가' 버튼을 눌러 담아 주세요.")


class CartState(TypedDict, total=False):
    response_style: str
    user_id: int
    selected_member_ids: list[int]
    message: str
    intent: dict
    parser: str
    constraints: dict   # profiles, avoid, strict, extra_excluded, terms, budget, count
    cart: list[dict]
    stats: dict
    validation_diagnostics: list[dict]
    notes: list[str]
    group_summary: list[str]  # 묶음별 담긴 개수 (예: ['점심 2', '과자 0'])
    reply: str
    asked: bool  # 이번 턴에 추천 대신 되물었는지
    notes_after: list[str]  # 추천 목록 아래에 붙일 안내 (예: '통과한 상품이 3개뿐이라 5개를 채우지 못했어요')
    history: list[dict]  # 이전 대화 [{user, bot, asked}] — LLM이 '그거'·되물음의 답을 이해하는 데만 쓴다
    shown: dict  # 같은 대화에서 요청(종류·상황)별로 이미 보여 준 상품 id — 다시 요청하면 새 상품을 먼저 보여 준다
    workflow: list[str]  # 이번 턴에 지나간 노드 순서 (디버깅·테스트용, 화면에는 쓰지 않는다)
    next_node: str  # 슈퍼바이저가 고른 다음 노드


def parse_intent(state: CartState) -> dict:
    members = db.list_members(state["user_id"])
    parsed, parser = intent_parser.parse(state["message"], [m["name"] for m in members], state.get("cart", []),
                                         profiles.mention_vocabulary(members), state.get("history", []))
    pending = state.get("constraints", {}).get("pending_allergy_intent")
    if pending:
        from .allergy_scope import scan
        scope = scan(state["message"], confirmation=True)
        if scope["avoid"] and not scope["unresolved_mentions"]:
            parsed = {**intent_parser.merge_pending(pending, parsed), "excluded_allergens": list(dict.fromkeys(pending.get("excluded_allergens", []) + parsed.get("excluded_allergens", []) + scope["avoid"])),
                      "unresolved_mentions": [], "needs_allergen_confirmation": False}
        else:
            parsed = {**intent_parser.merge_pending(pending, parsed), "needs_allergen_confirmation": True}
    pending_profile = state.get("constraints", {}).get("pending_profile_intent")
    if pending_profile and not pending:
        raw, _ = intent_parser._find_mentions(state["message"], profiles.mention_vocabulary(members))
        parsed = {**intent_parser.merge_pending(pending_profile, parsed), "target_profiles": parsed.get("target_profiles") or raw,
                  "excluded_target_profiles": [],
                  "expected_target_count": (state.get("constraints", {}).get("target_scope") or {}).get("expected_count")}
        if not parsed["target_profiles"]:
            parsed["target_profiles"] = pending_profile.get("target_profiles", [])
        old_scope = state.get("constraints", {}).get("target_scope") or {}
        if old_scope.get("exclude_mentions"):
            parsed["excluded_target_profiles"] = raw
            parsed["target_profiles"] = old_scope.get("include_mentions", [])
            if not raw:
                parsed["excluded_target_profiles"] = old_scope["exclude_mentions"]
    from .allergy_scope import GROUPS
    scope_notes = ["갑각류는 현재 지원하는 게·새우를 회피 조건에 적용했어요. 그 밖의 종은 판정할 수 없어요."] if any(
        word.casefold() in GROUPS for word in parsed.get("original_mentions", [])) else []
    return {"intent": parsed, "parser": parser, "notes": scope_notes, "notes_after": [], "stats": {}, "reply": "", "asked": False,
            "group_summary": [], "validation_diagnostics": []}


def resolve_conditions(state: CartState) -> dict:
    """대상 프로필과 회피 알레르겐(합집합), 예산·개수·상품 유형 조건을 확정한다."""
    intent = state["intent"]
    previous = state.get("constraints", {})
    members = db.list_members(state["user_id"])
    if intent.get("validation_issues"):
        return {"constraints": {**previous, "ask": "\n".join(intent["validation_issues"])}}
    if intent.get("needs_allergen_confirmation"):
        unresolved = ", ".join(intent.get("unresolved_mentions", []))
        return {"constraints": {**previous, "pending_allergy_intent": intent,
                "ask": f"피해야 할 알레르겐의 세부 범위를 확인해 주세요: {unresolved}. 예: '게와 새우', '호두만'. 현재 지원하는 39종 밖의 성분은 판정할 수 없습니다."}}

    # 사람 표현 → 프로필 연결은 별칭 표(규칙)로 한다. 없거나 애매하면 추천하지 않고 되묻는다.
    resolved, target_scope = profiles.resolve_scope(state["message"], intent["target_profiles"], members,
                                                     intent.get("excluded_target_profiles"))
    expected = intent.get("expected_target_count")
    if expected is not None and len({m["id"] for _, m, _ in resolved["matched"]}) != expected:
        target_scope["issues"].append(f"{expected}명 대상의 정확한 프로필 이름을 모두 알려 주세요.")
        target_scope["expected_count"] = expected
    ask = profiles.ask_message(resolved, members)
    if ask or target_scope["issues"]:
        return {"constraints": {**previous, "ask": ask or "\n".join(target_scope["issues"]),
                "pending_profile_intent": intent, "target_scope": target_scope}}
    mentioned = list({m["id"]: m for _, m, _ in resolved["matched"]}.values())
    selected = [m for m in members if m["id"] in state.get("selected_member_ids", [])]
    # 이번 문장에 사람이 없고 선택 칩도 없으면, 같은 대화에서 직전에 정한 대상을 이어 쓴다 (답변 첫 줄에 대상이 항상 보임)
    kept = [m for m in members if m["id"] in previous.get("member_ids", [])]
    targets = mentioned or selected or kept

    is_new = intent["action"] == "create_cart"
    # '다른 거 추천해줘'·'다시 추천해줘'처럼 종류를 새로 말하지 않은 재요청: 직전 조건 그대로, 보여 준 상품은 뒤로
    again = bool(AGAIN_PATTERN.search(state["message"])) and bool(previous.get("profiles") or previous.get("terms")) \
        and intent["item_index"] is None and not intent.get("from_keyword") and not intent.get("to_keyword") \
        and not _real_terms([intent["category_or_purpose"], *intent["keywords"]]) and len(intent.get("groups") or []) < 2
    if again:
        is_new = False
        intent = {**intent, "action": "create_cart"}
    extra = set(intent["excluded_allergens"])
    if not is_new:
        extra |= set(previous.get("extra_excluded", []))
    combined = combine_profiles(targets, sorted(extra))

    terms = _real_terms([intent["category_or_purpose"], *intent["keywords"]])  # 중복·일반적인 말 제거
    # 묶음: '점심이랑 과자' → [점심], [과자]. 한 종류면 묶음 하나.
    groups = [{"label": g["label"], "terms": _real_terms([g["label"], *g.get("keywords", [])]),
               "count": g.get("count")} for g in intent.get("groups") or [] if g.get("label")]
    if len(groups) < 2:
        groups = [{"label": None, "terms": terms, "count": None}]
    # 상황(소풍 등)은 새 추천 요청에서 말했을 때만 정하고, 그 추천을 고치는 동안에만 유지한다.
    # 새 추천 요청이 오면 그 문장에 상황이 없으면 비워진다 → 예전 상황이 다음 장보기에 끼어들지 않는다.
    situation = (situations.build(intent.get("situation"), intent.get("situation_prefer"), intent.get("situation_avoid"), terms)
                 if is_new else previous.get("situation"))
    constraints = {
        **combined,
        "ask": None,
        "pending_allergy_intent": None,
        "pending_profile_intent": None,
        "target_scope": target_scope,
        "original_mentions": intent.get("original_mentions", []),
        "unresolved_mentions": intent.get("unresolved_mentions", []),
        "member_ids": [m["id"] for m in targets],
        "kept_targets": not mentioned and not selected and bool(kept),
        "understood": profiles.understood_message(resolved) if mentioned else None,
        "extra_excluded": sorted(extra),
        "terms": terms if is_new else previous.get("terms", []),
        "groups": groups if is_new else previous.get("groups", [{"label": None, "terms": previous.get("terms", []), "count": None}]),
        "situation": situation,
        # LLM 선택(selector)에 넘길 요청 문장과 취향. 상황처럼 새 추천에서만 정한다.
        "request": intent_parser._ground_text(state["message"], state.get("history", []))
                   if is_new else previous.get("request", ""),
        "preferences": (intent.get("preferences") or []) if is_new else previous.get("preferences", []),
        "budget": _budget_of(intent) if is_new or intent["budget"] else previous.get("budget"),
        "count": intent["item_count"] if is_new or intent["item_count"] else previous.get("count"),
    }
    constraints["again"] = again
    return {"constraints": constraints, "intent": intent}


def _budget_of(intent: dict) -> dict | None:
    """LLM·규칙 파서가 뽑은 예산을 조건 묶음으로 (금액·종류·하한·개당). 금액이 없으면 None."""
    if not intent.get("budget"):
        return None
    mode = intent.get("budget_mode") or "max"
    low = intent.get("budget_min")
    if low and low < intent["budget"]:
        mode = "range"
    return {"amount": intent["budget"], "mode": mode, "min": low if mode == "range" else None,
            "per_item": bool(intent.get("per_item"))}


def supervisor(state: CartState) -> dict:
    """관리자 노드: 다음 작업을 정한다. 분기 규칙은 route()에 그대로 둔다 (규칙을 바꾸지 않음)."""
    return {"next_node": route(state)}


def route(state: CartState) -> str:
    action = state["intent"]["action"]
    constraints = state["constraints"]
    if constraints.get("ask"):
        return "ask_profile"
    if action == "question":
        return "answer"
    if action in EDIT_ACTIONS and state.get("cart"):
        return "edit_cart"
    return "build_cart"


def ask_profile(state: CartState) -> dict:
    if state["constraints"].get("ask"):
        suffix = "\n대상을 정확히 확인한 뒤 상품을 골라 드릴게요." if state.get("response_style") == "friendly" else ""
        return {"reply": state["constraints"]["ask"] + suffix, "asked": True}
    return {"reply": "누구를 위한 장보기인가요? 상단에서 가족 프로필을 선택하거나, "
                     "\"첫째 간식 5개\"처럼 프로필 이름을 함께 말씀해 주세요. "
                     "프로필이 없다면 '가족 프로필' 탭에서 먼저 추가해 주세요.", "asked": True}


def _group_counts(groups: list[dict], total: int | None) -> list[int]:
    """묶음별 개수. 묶음에 개수가 없으면 전체 개수를 나누고, 그것도 없으면 묶음마다 2개(묶음이 하나면 5개)."""
    if len(groups) == 1:
        return [groups[0]["count"] or total or cart_composer.DEFAULT_COUNT]
    if all(g["count"] for g in groups):
        return [g["count"] for g in groups]
    if total:
        base, extra = divmod(total, len(groups))
        return [g["count"] or base + (1 if i < extra else 0) for i, g in enumerate(groups)]
    return [g["count"] or 2 for g in groups]


def build_cart(state: CartState) -> dict:
    c = state["constraints"]
    groups = c.get("groups") or [{"label": None, "terms": c["terms"], "count": None}]
    counts = _group_counts(groups, c["count"])
    multi = len(groups) > 1
    cart: list[dict] = []
    notes: list[str] = []
    notes_after: list[str] = []  # 개수·예산 부족처럼 추천 목록에 대한 안내는 목록 아래에 보여 준다
    stats = {"matched": 0, "situation_excluded": 0, PASS: 0, BLOCK: 0, UNVERIFIED: 0}
    summary = []
    picked_by: list[str] = []  # LLM이 고른 묶음의 모델
    shown = dict(state.get("shown") or {})
    for group, count in zip(groups, counts):
        prefix = f"[{group['label']}] " if multi else ""
        missing = cart_composer.missing_terms(group["terms"])
        if group["terms"] and len(missing) == len(group["terms"]):
            # 상품 DB에 아예 없는 유형은 다른 상품으로 몰래 채우지 않고 알린다
            notes.append(f"{prefix}'{', '.join(missing)}'은(는) 지금 상품 DB에 없어서 추천하지 못했어요. "
                         "다른 상품 종류로 찾아보세요.")
            summary.append(f"{group['label']} 0")
            continue
        if missing:
            notes.append(f"{prefix}'{', '.join(missing)}'에 맞는 상품은 지금 상품 DB에 없어 나머지 조건으로 골랐어요.")
        budget = cart_composer.split_budget(c["budget"], count / sum(counts))  # 총액 예산은 개수 비율로 나눈다
        candidates, group_stats = cart_composer.search(group["terms"], c["avoid"], c["strict"],
                                                      exclude_ids={i["id"] for i in cart}, situation=c.get("situation"),
                                                      user_id=state["user_id"])
        # 같은 대화에서 같은 요청(종류·상황)으로 이미 보여 준 상품은 뒤로 돌린다 → 다시 요청하면 새 상품부터
        shown_key = "|".join(group["terms"]) + "@" + ((c.get("situation") or {}).get("name") or "")
        seen_ids = set(shown.get(shown_key, []))
        fresh = [item for item in candidates if item["id"] not in seen_ids]
        if seen_ids and len(fresh) < len(candidates):
            candidates = fresh + [{**item, "seen": True, "score": item.get("score", 0) - 10}
                                  for item in candidates if item["id"] in seen_ids]
            if len(fresh) < count:
                notes_after.append(prefix + (f"앞에서 추천하지 않은 상품은 {len(fresh)}개뿐이라 나머지는 앞에서 추천한 상품으로 채웠어요."
                                             if fresh else "새로 보여 드릴 상품이 더 없어 앞에서 추천한 상품을 다시 보여 드려요."))
        picked = None
        if candidates and selector.should_use(c.get("situation"), c.get("preferences")):
            request = c.get("request", "") + (f" (이번 묶음: {group['label']})" if multi else "")
            picked = selector.select(candidates, count, budget, request, c.get("situation"), c.get("preferences"),
                                     c.get("profiles"))
        if picked:
            items, group_notes, model = picked
            picked_by.append(model)
        else:
            items, group_notes = cart_composer.build(candidates, count, budget)
        if not candidates:
            group_notes = ["조건에 맞으면서 알레르기 검증을 통과한 상품이 없어요. 상품 유형을 바꿔 보시겠어요?"]
        for key in stats:
            stats[key] += group_stats[key]
        (notes_after if items else notes).extend(prefix + n for n in group_notes)
        items = [{k: v for k, v in item.items() if k != "seen"} for item in items]
        shown[shown_key] = list(dict.fromkeys([*shown.get(shown_key, []), *(item["id"] for item in items)]))
        cart.extend({**item, "group": group["label"]} if multi else item for item in items)
        summary.append(f"{group['label']} {len(items)}")
    stats["picked_by"] = picked_by[0] if picked_by else None
    return {"cart": cart, "stats": stats, "notes": notes, "notes_after": notes_after,
            "group_summary": summary if multi else [], "shown": shown}


def _matches(item: dict, keyword: str) -> bool:
    return keyword in f"{item['category']} {item['name']}"


def edit_cart(state: CartState) -> dict:
    intent, c = state["intent"], state["constraints"]
    cart = list(state["cart"])
    notes: list[str] = []
    action = intent["action"]
    index = intent["item_index"]

    def pick(terms: list[str], how_many: int, cheap_first: bool = False,
             min_score: int = cart_composer.STRONG_MATCH) -> list[dict]:
        """장바구니에 없는 PASS 후보 중 남은 예산 안에서 고른다. (더 싼 상품을 찾을 땐 예산 검사 생략)

        '피자로 바꿔줘'처럼 콕 집어 말한 상품은 상품명·소분류가 맞는 것만 고른다 (min_score).
        """
        candidates, _ = cart_composer.search(terms, c["avoid"], c["strict"], exclude_ids={item["id"] for item in cart},
                                            cheap_first=cheap_first, min_score=min_score, situation=c.get("situation"),
                                            user_id=state["user_id"])
        chosen = []
        for candidate in candidates:
            if len(chosen) >= how_many:
                break
            if cheap_first or cart_composer.fits(cart + chosen, candidate, c["budget"]):
                chosen.append(candidate)
        return chosen

    def removed_targets() -> list[dict]:
        if index is not None:
            if 1 <= index <= len(cart):
                return [cart[index - 1]]
            notes.append(f"{index}번 상품이 없어요. 현재 추천 상품은 {len(cart)}개예요.")
            return []
        if intent["from_keyword"]:
            targets = [item for item in cart if _matches(item, intent["from_keyword"])]
            if not targets:
                notes.append(f"추천 상품에 '{intent['from_keyword']}'에 해당하는 상품이 없어요.")
            return targets
        notes.append("어떤 상품인지 번호로 알려 주세요. 예) \"2번 상품 빼줘\"")
        return []

    if action == "remove_item":  # Optional cleanup of the chatbot's recommendation list.
        for target in removed_targets():
            cart.remove(target)
            notes.append(f"'{target['name']}'을(를) 추천 목록에서 숨겼어요.")

    elif action == "replace_item":
        targets = removed_targets()
        for target in targets:
            cart.remove(target)
        for target in targets:
            # 교체 대상을 말하지 않았으면 같은 소분류에서 찾는다.
            terms = [intent["to_keyword"]] if intent["to_keyword"] else [target["category"]]
            replacement = [r for r in pick(terms, 2) if r["id"] != target["id"]][:1]
            if replacement:
                cart.extend(replacement)
                notes.append(f"'{target['name']}' → '{replacement[0]['name']}'(으)로 바꿨어요.")
            else:
                wanted = f"'{intent['to_keyword']}' 중에서 " if intent["to_keyword"] else ""
                notes.append(f"{wanted}'{target['name']}'을(를) 대신할 알레르기 검증 통과 상품을 예산 안에서 "
                             "찾지 못했어요. 상품 유형이나 예산을 바꿔 보시겠어요?")

    elif action == "add_item":
        if intent["to_keyword"]:
            added = pick([intent["to_keyword"]], intent["item_count"] or 1)
        else:  # 처음 요청한 조건(목적어 포함)으로 더 고른다
            added = pick(c["terms"], intent["item_count"] or 1, min_score=1)
        cart.extend(added)
        wanted = f"'{intent['to_keyword']}' 중에서 " if intent["to_keyword"] else ""
        notes.append(f"{', '.join(a['name'] for a in added)}을(를) 추가했어요." if added else
                     f"{wanted}알레르기 검증을 통과하면서 예산 안에 드는 상품을 찾지 못했어요. "
                     "다른 상품 유형이나 예산으로 다시 말씀해 주세요.")

    elif action == "cheaper":
        swapped = 0
        for position, item in enumerate(list(cart)):
            cheaper = [r for r in pick([item["category"]], 1, cheap_first=True) if r["price"] < item["price"]]
            if cheaper:
                cart[position] = cheaper[0]
                swapped += 1
        notes.append(f"{swapped}개 상품을 같은 유형의 더 저렴한 상품으로 바꿨어요." if swapped else
                     "같은 유형에서 더 저렴한 검증 통과 상품이 없어요. 개수나 상품 유형을 바꿔 보시겠어요?")

    return {"cart": cart, "notes": notes}


def revalidate(state: CartState) -> dict:
    c = state["constraints"]
    diagnostics = []
    original = state.get("cart", [])
    cart, _ = cart_composer.revalidate(original, c["avoid"], c["strict"], diagnostics=diagnostics)
    # Product-level rejection reasons stay in diagnostics, not chatbot messages.
    notes = []
    _, (lo, hi) = cart_composer.budget_band(c["budget"])
    if cart and not lo <= cart_composer.total(cart) <= hi:
        cart = []
        notes.append("검증된 상품으로는 요청하신 예산 조건을 맞추지 못했어요.")
    notes_after = state.get('notes_after', [])
    if len(cart) < len(original):
        notes_after = [f"최종 확인된 상품은 {len(cart)}개로, 요청한 목록을 모두 채우지 못했어요."] if cart else []
    return {"cart": cart, "notes": state.get("notes", []) + notes, "validation_diagnostics": diagnostics, 'notes_after': notes_after,
            "group_summary": [f"{label} {sum(i.get('group') == label for i in cart)}" for label in dict.fromkeys(i.get("group") for i in original) if label]}


def respond(state: CartState) -> dict:
    """사용자에게 보이는 답변. 대상·회피 알레르겐·상황 이름·추천 개수와 안내만 짧게 보여 준다.

    별칭 해석(understood)·상황 기준 낱말·검증 내역·LLM 고르기 여부는 화면에 쓰지 않고
    API 응답의 constraints·stats·picked_by로만 남긴다 (10/07 사용자 요청: 답변을 간단하게).
    """
    c, cart = state["constraints"], state["cart"]
    who = "·".join(c["profiles"]) if c["profiles"] else "요청 조건"
    avoid = ", ".join(c["avoid"]) if c["avoid"] else "없음"
    lines = [f"[{who}] 회피 알레르겐: {avoid}" + (" (혼입 가능 표시도 제외)" if c["strict"] else "")
             + (" · 직전 요청의 대상 그대로" if c.get("kept_targets") else "")]
    if c.get("situation"):
        lines.append(f"상황: {c['situation']['name']}")
    if cart:
        groups = f" ({' · '.join(state['group_summary'])})" if state.get("group_summary") else ""
        budget = f" · {cart_composer.describe_budget(c['budget'])}" if c["budget"] else ""
        lines.append(f"추천 상품 {len(cart)}개{groups}{budget}")
    lines.extend(state.get("notes", []))
    if not cart:
        lines.append("현재 조건으로 추천할 수 있는 상품을 찾지 못했어요. 회피 조건을 유지한 채 다른 상품 종류로 찾아보세요.")
    if cart and len(c["profiles"]) > 1:
        lines.append("이 대상들이 함께 먹을 수 있도록 회피 조건을 합친 공통 추천 목록이에요.")
    from .allergy_scope import GROUPS
    if any(w.casefold() in GROUPS for w in c.get("original_mentions", [])) and not any("갑각류는 현재" in line for line in lines):
        lines.append("갑각류는 현재 지원하는 게·새우를 회피 조건에 적용했어요. 그 밖의 종은 판정할 수 없어요.")
    if state.get("response_style") == "friendly":
        if cart:
            lines.append("원하시면 상품을 바꾸거나 예산을 조정해 드릴게요.")
        else:
            lines.append("현재 추천 목록이 비어 있어요. 위 안내를 확인해 주세요. 원하시면 다른 상품 종류로 다시 찾아 드릴게요.")
    return {"reply": "\n".join(lines)}


def answer(state: CartState) -> dict:
    if not intent_parser.llm_models():
        return {"reply": HELP_TEXT}
    messages = [
        ("system", "너는 마켓컬리 알레르기 맞춤 장보기 챗봇이다. 두세 문장으로 짧게 답한다. "
                   "특정 상품이 알레르기에 안전한지는 스스로 판단하지 말고, 프로필을 선택해 "
                   "상품 추천을 요청하면 별도 알레르기 검증기가 확인한다고 안내한다. 의학적 조언은 하지 않는다.\n" + HELP_TEXT),
        ("human", state["message"]),
    ]
    if state.get("response_style") == "friendly":
        messages[0] = ("system", messages[0][1] + "\n친근한 존댓말과 쉬운 표현을 사용하고, 사용자가 다음에 할 수 있는 행동을 한 가지 안내한다. 안전을 보장하거나 판정 결과를 추측하지 않는다.")
    try:
        result, _ = intent_parser.invoke_with_fallback(lambda llm: llm, messages)
        text = result.text  # langchain-core 버전에 따라 속성(문자열)이거나 메서드다
        return {"reply": text if isinstance(text, str) else text()}
    except Exception:
        return {"reply": HELP_TEXT}


HISTORY_KEEP = 6  # 대화 상태에 남겨 둘 이전 대화 수 (LLM에는 그중 최근 3개만 넘긴다)


def remember(state: CartState) -> dict:
    """이번 대화를 기록한다. 챗봇 답은 첫 부분만 줄여서 남긴다 (장바구니는 따로 넘어간다)."""
    bot = " / ".join(line for line in state.get("reply", "").splitlines() if line.strip())
    turn = {"user": state["message"], "bot": bot[:150], "asked": bool(state.get("asked"))}
    return {"history": (state.get("history", []) + [turn])[-HISTORY_KEEP:]}


def tracked(fn):
    """노드가 끝날 때 이름을 workflow에 남긴다. parse_intent는 턴의 시작이므로 기록을 비운다."""
    @functools.wraps(fn)
    def node(state: CartState) -> dict:
        base = [] if fn.__name__ == "parse_intent" else state.get("workflow", [])
        out = fn({**state, "workflow": base})
        return {**out, "workflow": base + [fn.__name__]}
    return node


def build_graph():
    graph = StateGraph(CartState)
    for node in (parse_intent, resolve_conditions, ask_profile, build_cart, edit_cart, revalidate, respond, answer,
                 remember):
        graph.add_node(node.__name__, tracked(node))
    graph.add_node("supervisor", tracked(supervisor))
    graph.add_edge(START, "parse_intent")
    graph.add_edge("parse_intent", "resolve_conditions")
    graph.add_edge("resolve_conditions", "supervisor")
    graph.add_conditional_edges("supervisor", lambda s: s["next_node"],
                                ["answer", "ask_profile", "edit_cart", "build_cart"])
    graph.add_edge("build_cart", "revalidate")
    graph.add_edge("edit_cart", "revalidate")
    graph.add_edge("revalidate", "respond")
    for last in ("respond", "answer", "ask_profile"):
        graph.add_edge(last, "remember")
    graph.add_edge("remember", END)
    return graph.compile(checkpointer=MemorySaver())


agent = build_graph()


def chat(user_id: int, thread_id: str, message: str, selected_member_ids: list[int], response_style: str | None = None) -> dict:
    response_style = response_style or os.getenv("KAC_REPLY_STYLE", "concise")
    if response_style not in {"concise", "friendly"}:
        raise ValueError("response_style must be concise or friendly")
    state = agent.invoke(
        {"user_id": user_id, "message": message, "selected_member_ids": selected_member_ids, "response_style": response_style},
        config={"configurable": {"thread_id": f"{user_id}:{thread_id}"}},
    )
    # 되물은 턴에는 이전 추천을 다시 보여 주지 않는다 (대화 상태에는 그대로 남아 이후 수정에 쓰인다)
    cart = [] if state.get("asked") else state.get("cart", [])
    return {"reply": state["reply"], "response_style": response_style, "cart": cart, "asked": bool(state.get("asked")), "intent": state["intent"],
            "parser": state["parser"], "llm": intent_parser.last_model or intent_parser.llm_model_name(),
            "picked_by": (state.get("stats") or {}).get("picked_by"),
            "notes_after": [] if state.get("asked") else state.get("notes_after", []),
            "constraints": state.get("constraints", {}), "workflow": state.get("workflow", [])}
