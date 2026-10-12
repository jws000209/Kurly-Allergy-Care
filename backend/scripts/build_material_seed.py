"""kurly_products_v2_material_facility.csv → 상품 시드(JSON, 39종 판정용). 추천 알고리즘 확인용.

사용 (backend 폴더에서):
    .venv\\Scripts\\python scripts\\build_material_seed.py

결과:
    ../../마켓컬리 데이터/kurly_products_v2_material_seed.csv  (import 입력, 중간 파일)
    data/kurly_products_material.json                         (서버 시드. 기본 시드 kurly_products.json은 건드리지 않음)

규칙 (판정 안전 기준):
- 원재료명(OCR)은 단독으로 '확인됨'을 만들지 않는다. kurly 공식 allergy_info가 있는 상품에만
  원재료명을 ingredients로 넣는다. 공식 표시가 없는 상품은 원재료명을 ingredients_ocr 열에만 남긴다.
  (원재료명이 있으면 Rule Engine이 '확인됨'으로 보므로, OCR 누락이 PASS로 바뀌는 것을 막기 위함)
- 제조시설 열(라벨 OCR 19종)은 '같은 제조시설' 문구로 바꿔 allergy_label에 덧붙인다 (혼입 가능, 엄격 모드에서만 차단).
- 공식 표시도 없고 원재료명도 없는 상품은 판정할 수 없으므로 시드에서 빠진다 (import 스크립트 기본 동작).
"""
import csv
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
DATA = BACKEND.parents[1] / "마켓컬리 데이터"
SRC = DATA / "kurly_products_v2_material_facility.csv"
MID = DATA / "kurly_products_v2_material_seed.csv"
SEED = BACKEND / "data" / "kurly_products_material.json"


def build_mid():
    with open(SRC, encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        rows = list(reader)
    base = header[:-2]  # 마지막 두 열(원재료명·제조시설)은 헤더가 깨져 위치로 읽는다
    out_cols = base + ["ingredients_text", "ingredients_ocr", "facility_allergens"]
    ai_i = base.index("allergy_info")
    n_ing = n_fac = 0
    with open(MID, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(out_cols)
        for r in rows:
            info = r[ai_i] or ""
            raw = r[-2] or ""
            fac = [x.strip() for x in (r[-1] or "").split(",") if x.strip()]
            ingredients = raw if info.strip() else ""  # 공식 표시가 있을 때만 원재료명 사용
            n_ing += bool(ingredients)
            if fac:
                info = (info.rstrip() + f"\n- {', '.join(fac)}을(를) 사용한 제품과 같은 제조시설에서 제조하고 있습니다.").strip()
                n_fac += 1
            w.writerow(r[:ai_i] + [info] + r[ai_i + 1:len(base)]
                       + [ingredients, raw if not info.strip() or not ingredients else "", ", ".join(fac)])
    print(f"중간 CSV: {len(rows)}행 | 원재료명 사용 {n_ing} | 제조시설 덧붙임 {n_fac}")


def build_seed():
    code = (
        "import sys, pathlib; sys.path.insert(0, str(pathlib.Path('scripts').resolve().parent)); "
        "import importlib.util as u; "
        "spec = u.spec_from_file_location('imp', 'scripts/import_kurly_csv.py'); m = u.module_from_spec(spec); "
        "spec.loader.exec_module(m); m.OUTPUT = pathlib.Path(sys.argv[1]); "
        "sys.argv = ['x', sys.argv[2]]; m.main()"
    )
    subprocess.run([sys.executable, "-c", code, str(SEED), str(MID)], cwd=BACKEND, check=True)


if __name__ == "__main__":
    build_mid()
    build_seed()
