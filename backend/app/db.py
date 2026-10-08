"""Local Product DB + 계정·가족 프로필 저장소 (SQLite)."""
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from .extractor import extract

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv("KAC_DB_PATH", BASE_DIR / "data" / "kac.sqlite3"))
# 상품 시드: 팀이 수집한 컬리 상품 (scripts/import_kurly_csv.py 로 생성)
SEED_PATH = Path(os.getenv("KAC_SEED_PATH", BASE_DIR / "data" / "kurly_products.json"))
# 장바구니에 담을 때 컬리 상품 페이지에서 읽은 실제 최소 구매 수량 {상품 id: 수량}. 시드보다 우선한다.
LIVE_MIN_EA_PATH = Path(os.getenv("KAC_LIVE_MIN_EA_PATH", BASE_DIR / "data" / "min_ea_live.json"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL
);
CREATE TABLE IF NOT EXISTS members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    name TEXT NOT NULL,
    allergens TEXT NOT NULL DEFAULT '[]',
    strict INTEGER NOT NULL DEFAULT 0,
    relation TEXT NOT NULL DEFAULT '',
    rel_order TEXT NOT NULL DEFAULT '',
    aliases TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS products (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    price INTEGER,
    category TEXT NOT NULL DEFAULT '',
    ingredients TEXT NOT NULL DEFAULT '',
    allergy_label TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    image TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'mock',
    contains TEXT NOT NULL DEFAULT '[]',
    cross TEXT NOT NULL DEFAULT '[]',
    verified INTEGER NOT NULL DEFAULT 0,
    last_checked_at TEXT NOT NULL,
    min_ea INTEGER NOT NULL DEFAULT 1,
    review_count INTEGER,
    sales_rank INTEGER
);
"""


@contextmanager
def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """상품 시드를 DB에 맞춘다. 프로필·계정은 건드리지 않는다.

    - 시드에 없는 상품은 넣고, 내용(이름·가격·분류·표시·주소)이 바뀐 상품은 시드 값으로 고친다.
    - 시드에서 빠진 컬리 상품(source='kurly')은 지운다 → 새로 수집한 데이터로 바꾸면 예전 상품이 남지 않는다.
    - 사용자가 컬리 페이지에서 판정해 저장한 상품(source='kurly-page')은 시드에 없으면 그대로 두고,
      시드에 있으면 시드 값으로 맞춘다 (페이지 저장분은 가격이 비어 있어 수집 데이터가 더 완전하다).
    """
    with connect() as conn:
        conn.executescript(SCHEMA)
        # 예전 DB에 관계·순서·별칭 열이 없으면 추가한다 (기존 프로필은 그대로 둔다)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(members)")}
        for column, ddl in [("relation", "TEXT NOT NULL DEFAULT ''"), ("rel_order", "TEXT NOT NULL DEFAULT ''"),
                            ("aliases", "TEXT NOT NULL DEFAULT '[]'")]:
            if column not in columns:
                conn.execute(f"ALTER TABLE members ADD COLUMN {column} {ddl}")
        # 예전 DB에 최소 구매 수량·후기 수·판매량 순위 열이 없으면 추가한다
        product_columns = {row["name"] for row in conn.execute("PRAGMA table_info(products)")}
        for column, ddl in [("min_ea", "INTEGER NOT NULL DEFAULT 1"), ("review_count", "INTEGER"), ("sales_rank", "INTEGER")]:
            if column not in product_columns:
                conn.execute(f"ALTER TABLE products ADD COLUMN {column} {ddl}")
        existing = {row["id"]: dict(row) for row in conn.execute(f"SELECT id, {', '.join(_SEED_FIELDS)} FROM products")}
    if not SEED_PATH.exists():
        return
    seed = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    live = _live_min_ea()
    seed = [{**p, "min_ea": live[str(p["id"])]} if str(p["id"]) in live else p for p in seed]
    seed_ids = {str(product["id"]) for product in seed}
    with connect() as conn:
        if all(product.get("source", "mock") != "mock" for product in seed):
            conn.execute("DELETE FROM products WHERE source = 'mock'")  # 실제 컬리 데이터면 시연용 가상 상품은 지운다
        stale = [pid for pid, row in existing.items() if row["source"] == "kurly" and pid not in seed_ids]
        conn.executemany("DELETE FROM products WHERE id = ?", [(pid,) for pid in stale])
    added = updated = 0
    for product in seed:
        product = {**product, "source": product.get("source", "mock")}
        row = existing.get(str(product["id"]))
        if row is None:
            added += 1
        elif _seed_values(row) == _seed_values(product):
            continue
        else:
            updated += 1
        upsert_product(product)
    if added or updated or stale:
        print(f"[db] 상품 시드 반영: 추가 {added}, 갱신 {updated}, 시드에서 빠져 삭제 {len(stale)}")
    refresh_extraction()


_SEED_FIELDS = ("name", "price", "category", "ingredients", "allergy_label", "url", "image", "source", "min_ea",
                "review_count", "sales_rank")


def _seed_values(product: dict) -> tuple:
    url = product.get("url") or f"https://www.kurly.com/search?sword={quote(product['name'])}"  # upsert_product와 같은 기본값
    def value(field):
        if field == "url":
            return url
        if field in ("price", "review_count", "sales_rank"):
            return product.get(field)
        if field == "min_ea":
            return product.get("min_ea") or 1
        return product.get(field) or ""
    return tuple(value(field) for field in _SEED_FIELDS)


def refresh_extraction() -> int:
    """저장된 원문(원재료명·알레르기 표시)으로 알레르겐을 다시 뽑는다. 추출 규칙이 바뀌어도 기존 상품에 반영되게."""
    changed = 0
    with connect() as conn:
        rows = conn.execute("SELECT id, ingredients, allergy_label, contains, cross, verified FROM products").fetchall()
        for row in rows:
            new = extract(row["ingredients"], row["allergy_label"])
            values = (json.dumps(new["contains"], ensure_ascii=False), json.dumps(new["cross"], ensure_ascii=False),
                      int(new["verified"]))
            if values != (row["contains"], row["cross"], row["verified"]):
                conn.execute("UPDATE products SET contains = ?, cross = ?, verified = ? WHERE id = ?", (*values, row["id"]))
                changed += 1
    if changed:
        print(f"[db] 추출 규칙 변경으로 {changed}개 상품 판정 근거를 다시 계산")
    return changed


# ---------- users / members ----------

def login(name: str) -> dict:
    with connect() as conn:
        conn.execute("INSERT OR IGNORE INTO users(name) VALUES (?)", (name,))
        row = conn.execute("SELECT id, name FROM users WHERE name = ?", (name,)).fetchone()
    return dict(row)


def _member(row: sqlite3.Row) -> dict:
    from .profiles import auto_aliases, order_of, relation_of

    member = {"id": row["id"], "user_id": row["user_id"], "name": row["name"],
              "allergens": json.loads(row["allergens"]), "strict": bool(row["strict"]),
              "relation": row["relation"], "order": row["rel_order"], "aliases": json.loads(row["aliases"])}
    # 관계·순서 칸이 비어 있는 예전 프로필은 이름('둘째 아들')에서 짐작한 값으로 별칭을 만든다
    member["auto_aliases"] = auto_aliases(member["name"], relation_of(member), order_of(member))
    return member


def list_members(user_id: int) -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM members WHERE user_id = ? ORDER BY id", (user_id,)).fetchall()
    return [_member(r) for r in rows]


def create_member(user_id: int, name: str, allergens: list[str], strict: bool,
                  relation: str = "", order: str = "", aliases: list[str] | None = None) -> dict:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO members(user_id, name, allergens, strict, relation, rel_order, aliases) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, name, json.dumps(allergens, ensure_ascii=False), int(strict), relation, order,
             json.dumps(aliases or [], ensure_ascii=False)),
        )
        row = conn.execute("SELECT * FROM members WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _member(row)


def update_member(member_id: int, name: str, allergens: list[str], strict: bool,
                  relation: str = "", order: str = "", aliases: list[str] | None = None) -> dict | None:
    with connect() as conn:
        conn.execute(
            "UPDATE members SET name = ?, allergens = ?, strict = ?, relation = ?, rel_order = ?, aliases = ? WHERE id = ?",
            (name, json.dumps(allergens, ensure_ascii=False), int(strict), relation, order,
             json.dumps(aliases or [], ensure_ascii=False), member_id),
        )
        row = conn.execute("SELECT * FROM members WHERE id = ?", (member_id,)).fetchone()
    return _member(row) if row else None


def delete_member(member_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM members WHERE id = ?", (member_id,))


# ---------- products ----------

def _product(row: sqlite3.Row) -> dict:
    product = dict(row)
    product["contains"] = json.loads(product["contains"])
    product["cross"] = json.loads(product["cross"])
    product["verified"] = bool(product["verified"])
    return product


def upsert_product(product: dict) -> dict:
    """원재료명에서 알레르겐을 추출해 함께 저장하고 갱신일을 기록한다."""
    extracted = extract(product.get("ingredients"), product.get("allergy_label"))
    url = product.get("url") or f"https://www.kurly.com/search?sword={quote(product['name'])}"
    with connect() as conn:
        conn.execute(
            """INSERT INTO products(id, name, price, category, ingredients, allergy_label, url, image,
                                    source, contains, cross, verified, last_checked_at, min_ea, review_count, sales_rank)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 name=excluded.name, price=excluded.price, category=excluded.category,
                 ingredients=excluded.ingredients, allergy_label=excluded.allergy_label,
                 url=excluded.url, image=excluded.image, source=excluded.source,
                 contains=excluded.contains, cross=excluded.cross, verified=excluded.verified,
                 last_checked_at=excluded.last_checked_at, min_ea=excluded.min_ea,
                 review_count=COALESCE(excluded.review_count, products.review_count),
                 sales_rank=COALESCE(excluded.sales_rank, products.sales_rank)""",
            (
                str(product["id"]), product["name"], product.get("price"),
                product.get("category") or "", product.get("ingredients") or "",
                product.get("allergy_label") or "", url, product.get("image") or "",
                product.get("source") or "mock",
                json.dumps(extracted["contains"], ensure_ascii=False),
                json.dumps(extracted["cross"], ensure_ascii=False),
                int(extracted["verified"]),
                datetime.now().isoformat(timespec="seconds"),
                int(product.get("min_ea") or 1),
                product.get("review_count"), product.get("sales_rank"),
            ),
        )
    return get_product(str(product["id"]))


def _live_min_ea() -> dict[str, int]:
    try:
        return {str(k): int(v) for k, v in json.loads(LIVE_MIN_EA_PATH.read_text(encoding="utf-8")).items()}
    except (FileNotFoundError, ValueError):
        return {}


def set_min_ea(product_id: str, min_ea: int) -> bool:
    """컬리 상품 페이지에서 확인한 최소 구매 수량을 반영한다. 바뀌었으면 True.

    수집 데이터는 판매 단위 글로 아는 상품만 최소 수량이 있어(나머지는 1), 담을 때 실제 값으로 고친다.
    서버를 다시 켜도 유지되도록 min_ea_live.json에도 적는다.
    """
    min_ea = max(1, int(min_ea))
    product = get_product(product_id)
    if product is None or (product.get("min_ea") or 1) == min_ea:
        return False
    with connect() as conn:
        conn.execute("UPDATE products SET min_ea = ? WHERE id = ?", (min_ea, product_id))
    live = _live_min_ea()
    live[str(product_id)] = min_ea
    LIVE_MIN_EA_PATH.write_text(json.dumps(live, ensure_ascii=False, indent=1), encoding="utf-8")
    return True


def get_product(product_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    return _product(row) if row else None


def list_products() -> list[dict]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM products ORDER BY id").fetchall()
    return [_product(r) for r in rows]
