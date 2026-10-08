"""아이템 협업 필터링 + 함께 구매한 상품 + TF-IDF 콘텐츠 추천.

예시 노트북의 코사인 유사도/가중 평균을 적용한다. 영화 예시와 같은
아이템 축을 비교하고, 콘텐츠는 학습 말뭉치가 필요 없는 TF-IDF로 표현한다.
이 모듈은 취향 점수만 계산한다. 알레르기 판정은 호출자가 수행한다.
"""
import math
import re
from collections import Counter, defaultdict
from functools import lru_cache

EVENT_WEIGHTS = {"view": 1.0, "like": 3.0, "cart": 4.0, "purchase": 5.0}
MODES = {"hybrid", "item", "user", "content"}


def _unit(vector: dict) -> dict:
    norm = math.sqrt(sum(v * v for v in vector.values()))
    return {k: v / norm for k, v in vector.items()} if norm else {}


def _dot(left: dict, right: dict) -> float:
    if len(left) > len(right):
        left, right = right, left
    return sum(value * right.get(key, 0.0) for key, value in left.items())


def interaction_matrix(interactions: list[dict]) -> dict[int, dict[str, float]]:
    """사용자×상품 행렬. 여러 행동 중 가장 강한 신호만 사용한다."""
    matrix = defaultdict(dict)
    for row in interactions:
        weight = EVENT_WEIGHTS.get(row["event"], 0)
        if weight:
            uid, pid = row["user_id"], str(row["product_id"])
            matrix[uid][pid] = max(matrix[uid].get(pid, 0), weight)
    return dict(matrix)


def item_scores(history: dict[str, float], matrix: dict, neighbors: int = 20) -> dict[str, float]:
    """예시와 동일한 아이템×사용자 코사인 유사도, 상위 k개 경험 아이템 가중 평균."""
    items = defaultdict(dict)
    for uid, ratings in matrix.items():
        for pid, weight in ratings.items():
            items[pid][uid] = weight
    vectors = {pid: _unit(values) for pid, values in items.items()}
    result = {}
    for pid, vector in vectors.items():
        similar = sorted(((_dot(vector, vectors[seen]), seen) for seen in history
                          if seen != pid and seen in vectors), key=lambda pair: (-pair[0], pair[1]))
        similar = [(sim, seen) for sim, seen in similar if sim > 0][:neighbors]
        norm = sum(sim for sim, _ in similar)
        if norm:
            result[pid] = sum(sim * history[seen] for sim, seen in similar) / norm / 5.0
    return result


def co_purchase_scores(anchors: set[str], interactions: list[dict], user_id: int) -> dict[str, float]:
    """기준 상품을 산 다른 계정들이 구매한 상품의 구매자 비율. 조회·담기는 구매로 세지 않는다."""
    purchases = defaultdict(set)
    for row in interactions:
        if row["event"] == "purchase":
            purchases[row["user_id"]].add(str(row["product_id"]))
    buyers = [bought for uid, bought in purchases.items() if uid != user_id and bought & anchors]
    if not buyers:
        return {}
    counts = Counter(pid for bought in buyers for pid in bought - anchors)
    return {pid: count / len(buyers) for pid, count in counts.items()}


def _tokens(text: str) -> list[str]:
    words = re.findall(r"[가-힣a-z]+", text.lower())
    # 붙여 쓴 한국어 상품명도 일부 일치하도록 낱말과 글자 2개 조각을 함께 사용.
    return [token for word in words for token in
            ["w:" + word, *("g:" + word[i:i + 2] for i in range(len(word) - 1))]]


@lru_cache(maxsize=2)
def _content_vectors(snapshot: tuple) -> dict:
    counts = {}
    df = Counter()
    for pid, name, category, ingredients in snapshot:
        tf = Counter(_tokens(name) * 2 + _tokens(category) * 3 + _tokens(ingredients))
        counts[pid] = tf
        df.update(tf.keys())
    n = len(counts)
    return {pid: _unit({term: (1 + math.log(count)) * (1 + math.log((1 + n) / (1 + df[term])))
                        for term, count in tf.items()}) for pid, tf in counts.items()}


def content_scores(products: list[dict], history: dict[str, float]) -> dict[str, float]:
    snapshot = tuple(sorted((str(p["id"]), p.get("name") or "", p.get("category") or "",
                             p.get("ingredients") or "") for p in products))
    vectors = _content_vectors(snapshot)
    profile = defaultdict(float)
    for pid, weight in history.items():
        for term, value in vectors.get(pid, {}).items():
            profile[term] += value * weight
    profile = _unit(profile)
    return {pid: max(0.0, min(1.0, _dot(profile, vector))) for pid, vector in vectors.items()} if profile else {}


def score_products(products: list[dict], interactions: list[dict], user_id: int,
                   mode: str = "hybrid", reference_product_id: str | None = None) -> dict[str, dict]:
    if mode not in MODES:
        raise ValueError("Unknown recommendation mode")
    matrix = interaction_matrix(interactions)
    history = ({reference_product_id: 5.0} if reference_product_id else matrix.get(user_id, {}))
    content = content_scores(products, history)
    collaborative = item_scores(history, matrix)
    anchors = ({reference_product_id} if reference_product_id else
               {str(r["product_id"]) for r in interactions if r["user_id"] == user_id and r["event"] == "purchase"})
    co_purchase = co_purchase_scores(anchors, interactions, user_id)
    result = {}
    for product in products:
        pid = str(product["id"])
        c, i, u = content.get(pid, 0.0), collaborative.get(pid, 0.0), co_purchase.get(pid, 0.0)
        if mode == "content":
            score, method = c, "content" if c else "popularity"
        elif mode == "user":
            score, method = (u, "co_purchase") if u else ((c, "content") if c else (0.0, "popularity"))
        elif mode == "item":
            score, method = (i, "item") if i else ((c, "content") if c else (0.0, "popularity"))
        else:
            signals = [(i, 0.4, "item"), (u, 0.3, "co_purchase"), (c, 0.3, "content")]
            active = [(value, weight, name) for value, weight, name in signals if value > 0]
            score = sum(value * weight for value, weight, _ in active) / sum(weight for _, weight, _ in active) if active else 0.0
            method = "hybrid" if len(active) > 1 else (active[0][2] if active else "popularity")
        result[pid] = {"recommendation_score": round(score, 8), "recommendation_method": method,
                       "content_score": round(c, 8), "item_score": round(i, 8), "co_purchase_score": round(u, 8)}
    return result


def personalize(candidates: list[dict], products: list[dict], interactions: list[dict], user_id: int,
                mode: str = "hybrid", reference_product_id: str | None = None,
                exclude_seen: bool = False) -> list[dict]:
    scores = score_products(products, interactions, user_id, mode, reference_product_id)
    seen = set(interaction_matrix(interactions).get(user_id, {})) if exclude_seen else set()
    if reference_product_id:
        seen.add(reference_product_id)
    items = [{**item, **scores[item["id"]]} for item in candidates if item["id"] not in seen]
    # 요청 관련도·상황·옵션을 먼저 유지하고, 그 안에서 취향 점수로 순서를 정한다.
    items.sort(key=lambda item: (-item.get("score", 0), not item.get("preferred", False),
                                item.get("needs_option", False), item.get("source") == "mock",
                                -item["recommendation_score"],
                                -(item.get("review_count") if item.get("review_count") is not None else -1),
                                item.get("sales_rank") if item.get("sales_rank") is not None else 10**6,
                                item["id"]))
    return items
