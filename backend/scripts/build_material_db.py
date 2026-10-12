"""원재료명 정제 결과를 SQLite 데이터베이스로 묶는다 (추천 확인·조회용).

출력: kurly-allergy-care/data/kurly_material.sqlite3  (서버 DB kac.sqlite3와 별개)

테이블
- products          상품 8,416행 (정제 원재료명·출처·삭제사유·교정 포함, 원본 열 그대로)
- direct_readings   직접 판독 결과 (파트 1·2·4). 파일 1장 = 1행
- derived_allergens 상품별 알레르기 (출처: 원재료 정제 39종 / kurly 함유 문구 19종 / 제조시설 19종)
- correction_log    원재료명 교정 내역

판정 기준이 아니다. 추천 알고리즘 확인용이다. 원재료명 OCR 재현율이 낮으므로 '없음'을 안전으로 보지 않는다.
"""
import csv
import json
import sqlite3
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

from build_material_allergy import load_dictionary, match, NAME19  # noqa: E402

ROOT = BACKEND.parents[1]
DATA = ROOT / "마켓컬리 데이터"
CLEAN = DATA / "kurly_products_v2_material_facility_clean.csv"
LOG = DATA / "원재료명_정제_로그.csv"
OUT = BACKEND / "data" / "kurly_material.sqlite3"
VISION = {
    "파트1": DATA / "알레르기판독_파트1" / "work" / "vision_part1.jsonl",
    "파트2": DATA / "알레르기 파트 모으기" / "vision_part2.json",
    "파트4": DATA / "알레르기 파트 모으기" / "vision_part4.jsonl",
}


def load_vision():
    rows = []
    for part, path in VISION.items():
        if path.suffix == ".json":
            records = json.load(open(path, encoding="utf-8"))
        else:
            records = [json.loads(line) for line in open(path, encoding="utf-8")]
        for r in records:
            rows.append((
                r["file"], r["file"].split("/")[1], part, r.get("원재료명"),
                "|".join(r.get("함유_알레르기") or []), "|".join(r.get("같은시설_알레르기") or []),
                1 if r.get("판독불가") else 0, r.get("비고"),
            ))
    return rows


def main():
    if OUT.exists():
        OUT.unlink()
    con = sqlite3.connect(OUT)
    cur = con.cursor()

    # 1) products
    with open(CLEAN, encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        body = list(reader)
    cols = [c.replace('"', "") for c in header]
    cur.execute("CREATE TABLE products (" + ", ".join(f'"{c}" TEXT' for c in cols) + ", PRIMARY KEY (product_id))")
    cur.executemany(f"INSERT INTO products VALUES ({','.join('?' * len(cols))})", body)
    pid_i = cols.index("product_id")
    clean_i = cols.index("원재료명_정제")
    fac_i = cols.index("제조시설")
    ai_i = cols.index("allergy_info")

    # 2) direct readings
    cur.execute("""CREATE TABLE direct_readings (
        file TEXT, product_id TEXT, 파트 TEXT, 원재료명 TEXT, 함유_알레르기 TEXT,
        같은시설_알레르기 TEXT, 판독불가 INTEGER, 비고 TEXT)""")
    cur.executemany("INSERT INTO direct_readings VALUES (?,?,?,?,?,?,?,?)", load_vision())
    cur.execute("CREATE INDEX ix_dr_pid ON direct_readings(product_id)")

    # 3) derived allergens
    rules = load_dictionary()
    derived = []
    for row in body:
        pid = row[pid_i]
        seen = set()
        for name, kind, auto, term in match(row[clean_i], rules):
            if auto and (name, "원재료정제") not in seen:
                seen.add((name, "원재료정제"))
                derived.append((pid, name, "원재료정제", kind, term))
        for line in row[ai_i].splitlines():
            if "함유" in line:
                for name in NAME19:
                    if name in line and (name, "kurly함유") not in seen:
                        seen.add((name, "kurly함유"))
                        derived.append((pid, name, "kurly함유", "MFDS", line.strip()))
        for name in [x.strip() for x in row[fac_i].split(",") if x.strip()]:
            if name in NAME19 and (name, "제조시설") not in seen:
                seen.add((name, "제조시설"))
                derived.append((pid, name, "제조시설", "MFDS", ""))
    # 원물: 원재료명이 없는 원물 상품은 상품명에서 찾은 알레르겐을 연결 (출처 '원물_이름')
    if "원물_이름_알레르겐" in cols:
        raw_i = cols.index("원물_이름_알레르겐")
        for row in body:
            if row[raw_i]:
                for name in row[raw_i].split("|"):
                    derived.append((row[pid_i], name, "원물_이름", "MFDS", row[cols.index("product_name")]))
    cur.execute("""CREATE TABLE derived_allergens (
        product_id TEXT, allergen TEXT, 출처 TEXT, 구분 TEXT, 근거 TEXT)""")
    cur.executemany("INSERT INTO derived_allergens VALUES (?,?,?,?,?)", derived)
    cur.execute("CREATE INDEX ix_da_pid ON derived_allergens(product_id)")

    # 4) correction log
    with open(LOG, encoding="utf-8-sig", newline="") as f:
        log = list(csv.reader(f))[1:]
    cur.execute("CREATE TABLE correction_log (product_id TEXT, 출처 TEXT, 원본 TEXT, 정제 TEXT, 교정 TEXT)")
    cur.executemany("INSERT INTO correction_log VALUES (?,?,?,?,?)", log)

    con.commit()
    for t in ["products", "direct_readings", "derived_allergens", "correction_log"]:
        print(t, cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
    con.close()
    print("저장:", OUT)


if __name__ == "__main__":
    main()
