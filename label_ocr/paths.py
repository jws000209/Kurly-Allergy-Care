"""경로 모음.

이 폴더(label_ocr)는 두 곳 어디에 있어도 된다.
- 저장소 안: 바탕화면/핵심프로젝트 AI/kurly-allergy-care/label_ocr  → 데이터는 옆 폴더 '마켓컬리 데이터'
- 데이터 폴더 안: 바탕화면/핵심프로젝트 AI/마켓컬리 데이터/label_ocr
"""
import gzip
from pathlib import Path

HERE = Path(__file__).resolve().parent
if (HERE.parent / "backend").exists():          # 저장소 안
    BACKEND = HERE.parent / "backend"
    BASE = HERE.parent.parent / "마켓컬리 데이터"
else:                                            # 마켓컬리 데이터 안
    BASE = HERE.parent
    BACKEND = BASE.parent / "kurly-allergy-care" / "backend"
# BACKEND: 알레르겐 사전 · 추출기 (Rule Engine과 같은 기준)
PRODUCTS = BASE / "kurly_products_v2.csv"       # 상품 목록 (카테고리 · 상품명 · allergy_info)
LABEL_TABLE = BASE / "label_images.csv"         # 표시사항 이미지 목록 (file 열은 BASE 기준 상대경로)

OCR_DIR = HERE / "ocr_output"
OCR_WINDOWS = OCR_DIR / "ocr_windows.jsonl"          # Windows OCR 컬러 1배 (전체)
OCR_WINDOWS_2X = OCR_DIR / "ocr_windows_2x.jsonl"    # Windows OCR 흑백 2배 (가공품)
OCR_RAPID = OCR_DIR / "ocr_rapid.jsonl"              # RapidOCR 흑백 + 줄 좌표 (가공품)
OCR_RAPID_EXTRA = OCR_DIR / "ocr_rapid_extra.jsonl"  # RapidOCR 보완: 3가지가 안 보인 상품만 회전·대비 보정
OCR_FILES = [OCR_WINDOWS, OCR_WINDOWS_2X, OCR_RAPID, OCR_RAPID_EXTRA]
RESULT_DIR = HERE / "result"


def _gz(path: Path) -> Path:
    return path.with_name(path.name + ".gz")


def ocr_exists(path: Path) -> bool:
    """OCR 결과 파일이 있는지 (.jsonl 또는 압축본 .jsonl.gz — 저장소에는 100MB 한도 때문에 압축본만 올린다)"""
    return path.exists() or _gz(path).exists()


def read_ocr_lines(path: Path) -> list[str]:
    """OCR 결과 JSONL의 줄들. .jsonl 이 없으면 .jsonl.gz 를 읽는다."""
    if path.exists():
        return path.read_text(encoding="utf-8").splitlines()
    if _gz(path).exists():
        with gzip.open(_gz(path), "rt", encoding="utf-8") as fh:
            return fh.read().splitlines()
    return []
