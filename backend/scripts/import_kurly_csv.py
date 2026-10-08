"""팀이 수집한 컬리 상품 CSV → 백엔드 상품 시드(JSON).

사용법 (backend 폴더에서):
    .venv\\Scripts\\python scripts\\import_kurly_csv.py CSV경로 [--blank-reviewed] [--add-gift-sets 원본CSV]
        [--min-ea 최소수량CSV] [--keep-unverified]
    - --min-ea: product_id,min_ea 열이 있는 보충 파일 (크롤러 노트북의 '최소 구매 수량 보충' 셀이 만든다)

지금 서비스 데이터 (10/07 결정):
    .venv\\Scripts\\python scripts\\import_kurly_csv.py "..\\..\\마켓컬리 데이터\\kurly_products_v2_final.csv"
        --blank-reviewed --add-gift-sets "..\\..\\마켓컬리 데이터\\kurly_products_v2.csv"
    - kurly_products_v2_final.csv: 팀 전처리본 (옵션 상품 제외, 알레르기 칸이 빈 상품은 원물·단순 구성만 남김)
    - --blank-reviewed: 그 전처리본의 빈 칸을 '표시 없음·원물 판단'(NO_LABEL_REVIEWED)으로 받는다
    - --add-gift-sets: 전처리에서 '선물세트'라는 이름 때문에만 빠진 상품(함유 표시가 있는 것)을 원본에서 되살린다

알레르기 표시로 판정할 수 없는 상품(비어 있음, '같은 제조시설' 문구만 있고 함유 표시 없음,
'옵션별 상이·상세페이지 참고' 같은 안내 문구)은 상품 시드에서 뺀다. 남기려면 --keep-unverified.

알레르겐은 CSV의 contains_allergens 열을 쓰지 않고, 서버의 Rule Engine과 같은 추출기로
allergy_info 원문에서 다시 뽑는다 (판정 기준을 한 곳으로 모으기 위해).
"""
import csv
import json
import re
import sys
import unicodedata
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.extractor import NO_LABEL_REVIEWED, extract  # noqa: E402  (서버와 같은 판정 근거 기준)

OUTPUT = BACKEND / "data" / "kurly_products.json"

# 표시문 뒤에 크롤링되어 붙은 페이지 문구는 잘라 낸다.
_TRAILING = re.compile(r"\s*(소비기한\s*\(|상품선택|안내사항).*$", re.S)

# 팀 전처리(kurly_preprocessing_history.md 5.8)와 같은 상품명 규칙
_OPTION = re.compile(r"(?<!\d)\d+\s*종|택\s*1(?!\d)")
_GIFT = re.compile(r"선물\s*세트|기프트\s*세트|gift\s*set", re.I)


def _int(value: str | None) -> int | None:
    try:
        return int(float(value)) if value and value.strip() else None
    except ValueError:
        return None


def _name(value: str) -> str:
    # v2 크롤러는 컬리 표기(간편식·밀키트·샐러드)를 그대로 저장한다. 기존 데이터·추천 기준은 '/'로 묶인 분류를 구분한다.
    return value.strip().replace("·", "/")


_MIN_EA_TEXT = re.compile(r"최소\s*(?:구매|주문)?\s*수량\s*(?:은|:)?\s*(\d+)")
MIN_EA: dict[str, int] = {}  # --min-ea CSV로 받은 상품별 최소 구매 수량 (컬리 minEa)


def _min_ea(row: dict) -> int:
    """최소 구매 수량: min_ea 열(크롤러 v2.1) > --min-ea 보충 파일 > 판매 단위 글('최소 구매 수량은 2봉') > 1."""
    value = _int(row.get("min_ea")) or MIN_EA.get(row["product_id"].strip())
    if not value:
        m = _MIN_EA_TEXT.search(row.get("sales_unit") or "")
        value = int(m.group(1)) if m else 1
    return max(1, value)


def convert(row: dict) -> dict:
    product = {
        "id": row["product_id"].strip(),
        "name": row["product_name"].strip(),
        "price": _int(row["original_price"]),
        "category": f"{_name(row['category'])}>{_name(row['subcategory'])}",
        "ingredients": (row.get("ingredients_text") or "").strip(),
        "allergy_label": _TRAILING.sub("", row.get("allergy_info") or "").strip(),
        "url": row["product_url"].strip(),
        "image": row["product_image_url"].strip(),
        "source": "kurly",
    }
    product["min_ea"] = _min_ea(row)
    # v2 크롤러(kurly_crawler_v2.ipynb)에만 있는 열. 아직 DB에는 저장하지 않고 시드에만 싣는다.
    if "sales_rank" in row:
        product.update(
            sales_rank=_int(row.get("sales_rank")),
            review_count=_int(row.get("review_count")),
            storage_types=json.loads(row.get("storage_types") or "[]"),
        )
    return product


def read(csv_path: Path) -> list[dict]:
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def gift_sets(original_csv: Path, have: set[str]) -> list[dict]:
    """원본에서 선물세트라서만 빠진 상품: 이름이 선물세트이고 옵션 상품이 아니며, 알레르기 칸에 함유 표시가 있는 것."""
    picked = []
    for row in read(original_csv):
        name = unicodedata.normalize("NFKC", row["product_name"])
        if row["product_id"] in have or not _GIFT.search(name) or _OPTION.search(name):
            continue
        product = convert(row)
        if extract(product["ingredients"], product["allergy_label"])["contains"]:
            picked.append(product)
    return picked


def verifiable(product: dict) -> bool:
    return extract(product["ingredients"], product["allergy_label"])["verified"]


def main() -> None:
    argv = sys.argv[1:]
    original = None
    if "--add-gift-sets" in argv:
        i = argv.index("--add-gift-sets")
        original = Path(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    if "--min-ea" in argv:
        i = argv.index("--min-ea")
        for row in read(Path(argv[i + 1])):
            if _int(row.get("min_ea")):
                MIN_EA[row["product_id"].strip()] = _int(row["min_ea"])
        argv = argv[:i] + argv[i + 2:]
        print(f"최소 구매 수량 보충 파일: {len(MIN_EA)}개")
    args = [a for a in argv if not a.startswith("--")]
    if not args:
        sys.exit("CSV 경로를 넣어 주세요. 사용법은 이 파일 맨 위 설명을 보세요.")
    products = [convert(row) for row in read(Path(args[0]))]

    if "--blank-reviewed" in argv:
        blank = [p for p in products if not p["allergy_label"] and not p["ingredients"]]
        for p in blank:
            p["allergy_label"] = NO_LABEL_REVIEWED
        print(f"빈 알레르기 칸 {len(blank)}개 → '표시 없음·원물 판단'으로 받음")
    if original:
        extra = gift_sets(original, {p["id"] for p in products})
        products += extra
        print(f"선물세트 {len(extra)}개를 원본에서 되살림")

    dropped = 0
    if "--keep-unverified" not in argv:
        kept = [p for p in products if verifiable(p)]
        dropped, products = len(products) - len(kept), kept
    multi = sum(p["min_ea"] > 1 for p in products)
    if multi:
        print(f"최소 구매 수량이 2개 이상인 상품 {multi}개 (가격·예산은 그 수량만큼 곱해 계산)")
    OUTPUT.write_text(json.dumps(products, ensure_ascii=False, indent=1), encoding="utf-8")
    note = f" (알레르기 표시로 판정할 수 없는 {dropped}개 제외)" if dropped else ""
    print(f"{len(products)}개 상품 → {OUTPUT}{note}")


if __name__ == "__main__":
    main()
