"""Read-only code audit plus isolated localhost API probes. Never uses real accounts or LLM keys."""
import json
import time
import uuid
from collections import Counter
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

BASE = "http://127.0.0.1:8000"
OUT = Path(__file__).with_name("audit_20261013.json")
results = {}


def call(path, body=None, method=None):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = Request(BASE + path, data=data, method=method or ("POST" if body is not None else "GET"),
                  headers={"Content-Type": "application/json"})
    started = time.perf_counter()
    try:
        with urlopen(req, timeout=30) as response:
            return response.status, json.load(response), round((time.perf_counter() - started) * 1000, 1)
    except HTTPError as error:
        return error.code, json.load(error), round((time.perf_counter() - started) * 1000, 1)


def must(path, body=None):
    status, data, _ = call(path, body)
    assert status == 200, (path, status, data)
    return data


suffix = uuid.uuid4().hex[:8]
user = must("/api/login", {"name": "audit-" + suffix})
uid = user["id"]


def member(name, allergens, strict=False):
    return must(f"/api/users/{uid}/members", {"name": name, "allergens": allergens, "strict": strict})


first = member("첫째", ["우유", "알류"])
dad = member("아빠", ["땅콩"], True)
egg = member("알류시험", ["알류"])
wheat = member("밀시험", ["밀"])
milk = member("우유시험", ["우유"])
strict_milk = member("엄격시험", ["우유"], True)
none = member("일반시험", [])

results["health"] = must("/api/health")
products = must("/api/products")
catalog = {p["id"]: p for p in products}
results["catalog"] = {"total": len(products), "sources": dict(Counter(p["source"] for p in products)),
                      "verified": sum(bool(p["verified"]) for p in products),
                      "reviewed_blank": sum(p["allergy_label"].startswith("[알레르기 표시 없음") for p in products),
                      "missing_price": sum(p["price"] is None for p in products)}


def say(key, message, ids, thread=None):
    status, data, elapsed = call("/api/chat", {"user_id": uid, "thread_id": thread or key + suffix,
                                               "message": message, "selected_member_ids": ids})
    assert status == 200, (key, status, data)
    avoid = set(data.get("constraints", {}).get("avoid", []))
    strict = data.get("constraints", {}).get("strict", False)
    cart = data.get("cart", [])
    violations = [p["id"] for p in cart if not catalog.get(p["id"], {}).get("verified")
                  or avoid & set(catalog[p["id"]]["contains"])
                  or (strict and avoid & set(catalog[p["id"]]["cross"]))]
    results[key] = {"request": message, "status": status, "elapsed_ms": elapsed,
                    "parser": data.get("parser"), "action": data["intent"]["action"],
                    "constraints": data.get("constraints"), "reply": data["reply"],
                    "notes_after": data.get("notes_after"), "cart": cart,
                    "allergen_violations_against_applied_conditions": violations,
                    "distinct_products": len(cart), "minimum_packages": sum(p.get("min_ea", 1) for p in cart),
                    "total": sum(p["price"] for p in cart)}
    print(key, json.dumps({"ms": elapsed, "count": len(cart), "avoid": sorted(avoid),
                           "violations": violations, "total": results[key]["total"]}, ensure_ascii=False), flush=True)
    return data


say("dumplings", "만두 5개", [first["id"]])
say("dumplings_wheat", "만두 5개", [wheat["id"]])
say("union", "만두 5개", [first["id"], dad["id"]])
say("budget", "만두 2만원 이내 5개", [first["id"]])
say("no_profile", "만두 5개", [])
say("missing_type", "사프란 5개 추천해줘", [first["id"]])
say("almond_request", "아몬드 없이 간식 5개", [first["id"]])
edit_thread = "edit" + suffix
say("edit_baseline", "만두 5개", [egg["id"]], edit_thread)
say("edit_add_avoid", "우유 없이 더 저렴하게", [egg["id"]], edit_thread)
say("remove", "2번 상품 빼줘", [egg["id"]], edit_thread)
say("profile_change", "더 저렴하게", [first["id"], dad["id"]], edit_thread)
say("zero_count", "만두 0개", [first["id"]])
say("negative_count", "만두 -1개", [first["id"]])


def check(key, ingredients, label, mid):
    status, data, elapsed = call("/api/check", {"user_id": uid, "member_ids": [mid], "save": False,
        "product": {"id": "audit-probe", "name": "가상 표시문 시험", "ingredients": ingredients,
                    "allergy_label": label}})
    results[key] = {"ingredients": ingredients, "label": label, "http_status": status,
                    "verdict": data, "elapsed_ms": elapsed}


check("check_block", "", "우유 함유", milk["id"])
check("check_cross_normal", "", "대두 함유. 우유 혼입 가능", milk["id"])
check("check_cross_strict", "", "대두 함유. 우유 혼입 가능", strict_milk["id"])
check("check_cross_only", "", "우유를 사용한 제품과 같은 제조시설에서 제조", milk["id"])
check("check_blank", "", "", milk["id"])
check("check_english", "milk, wheat flour", "", milk["id"])
check("check_non_ingredient", "상세페이지 참고", "", milk["id"])
check("check_negative", "", "우유 미함유", milk["id"])
check("check_label_non_ingredient", "", "상세페이지 참고", milk["id"])
status, data, _ = call(f"/api/users/{uid}/members", {"name": "아몬드시험", "allergens": ["아몬드"]})
results["unsupported_profile"] = {"status": status, "response": data}

# A page read currently upserts all fields. Reproduce only in this isolated audit database.
known = next(p for p in products if p["price"] and p["category"] and p["contains"] and p["allergy_label"]
             and not p["allergy_label"].startswith("[알레르기 표시 없음"))
before = {k: known[k] for k in ("id", "name", "price", "category", "allergy_label", "ingredients", "verified")}
must("/api/check", {"user_id": uid, "member_ids": [milk["id"]], "save": True,
    "product": {"id": known["id"], "name": known["name"], "price": None,
                "category": "", "allergy_label": "", "url": known["url"]}})
after = next(p for p in must("/api/products") if p["id"] == known["id"])
results["page_overwrite"] = {"before": before, "after": {k: after[k] for k in before}}

OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
print("Saved", OUT, flush=True)
