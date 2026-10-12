"""kurly_products_v2_material_facility.csv → 상품 시드(JSON, 39종 판정용). 추천 알고리즘 확인용.

사용 (backend 폴더에서):
    .venv\\Scripts\\python scripts\\build_material_seed.py

결과:
    ../../마켓컬리 데이터/kurly_products_v2_material_seed.csv  (import 입력, 중간 파일)
    data/kurly_products_material.json                         (서버 시드. 기본 시드 kurly_products.json은 건드리지 않음)

규칙 (판정 안전 기준):
- 판정 기준은 정제된 원재료명(원재료명_정제)이다. 원재료명이 없는 상품은 시드에서 제외된다 (원물 제외).
  allergy_info의 '함유' 문구는 원재료명이 있을 때 contains 계산에 함께 쓴다.
- 제조시설 열(라벨 OCR 19종)은 '같은 제조시설' 문구로 바꿔 allergy_label에 덧붙인다 (혼입 가능, 엄격 모드에서만 차단).
- 공식 표시도 없고 원재료명도 없는 상품은 판정할 수 없으므로 시드에서 빠진다 (import 스크립트 기본 동작).
"""
import csv
import subprocess
import sys
from pathlib import Path

NO_LABEL = "[알레르기 표시 없음 · 원물·단순 구성으로 19종 없음 판단]"

BACKEND = Path(__file__).resolve().parents[1]
DATA = BACKEND.parents[1] / "마켓컬리 데이터"
CLEAN = DATA / "kurly_products_v2_material_facility_clean.csv"
MID = DATA / "kurly_products_v2_material_seed.csv"
SEED = BACKEND / "data" / "kurly_products_material.json"


def build_mid():
    """정제된 원재료명(원재료명_정제)을 판정 입력으로 쓴다. 원재료명이 없는 상품은 ingredients가 비어 시드에서 빠진다.

    원물(원물여부='원물')은 원물 판단 표시를 붙이고, 상품명에서 찾은 알레르겐을 '- X 함유' 줄로 넣는다.
    """
    with open(CLEAN, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        base = reader.fieldnames
    out_cols = [c for c in base if c not in ("원재료명_원본", "원재료명_출처", "원재료명_삭제사유", "원재료명_교정")]
    out_cols += ["ingredients_text", "facility_allergens"]
    n_ing = n_raw = 0
    with open(MID, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(out_cols)
        for r in rows:
            info = (r.get("allergy_info") or "").strip()
            fac = [x.strip() for x in (r.get("제조시설") or "").split(",") if x.strip()]
            ingredients = (r.get("원재료명_정제") or "").strip()
            if r.get("원물여부") == "원물":
                n_raw += 1
                names = [x for x in (r.get("원물_이름_알레르겐") or "").split("|") if x]
                info = NO_LABEL + ("\n" + "\n".join(f"- {x} 함유" for x in names) if names else "")
                ingredients = ""
            if fac:
                info = (info.rstrip() + f"\n- {', '.join(fac)}을(를) 사용한 제품과 같은 제조시설에서 제조하고 있습니다.").strip()
            n_ing += bool(ingredients)
            w.writerow([r[c] if c not in ("allergy_info",) else info for c in base
                        if c not in ("원재료명_원본", "원재료명_출처", "원재료명_삭제사유", "원재료명_교정")]
                       + [ingredients, ", ".join(fac)])
    print(f"중간 CSV: {len(rows)}행 | 원재료명 사용 {n_ing} | 원물 {n_raw}")


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
