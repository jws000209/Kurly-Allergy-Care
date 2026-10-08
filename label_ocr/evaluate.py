"""정확도 확인: OCR로 뽑은 함유·같은 시설 알레르기를 컬리 텍스트 알레르기 정보(allergy_info)와 대조한다.

    python evaluate.py                         # ocr_output 의 판독 전부 합쳐서
    python evaluate.py ocr_output/ocr_rapid.jsonl   # 특정 판독만

정답(allergy_info)은 컬리가 텍스트로 준 것이라 함유·시설 문구가 빠진 상품도 있다.
그래서 여기서 '오탐'으로 세는 것 중 일부는 이미지에는 실제로 있는 알레르기다 (실제 정밀도는 이 수치보다 높다).
"""
import json
import sys
from pathlib import Path

import pandas as pd

import extract_label as E
import paths
from app.extractor import extract as backend_extract

files = [Path(f) for f in sys.argv[1:]] or [f for f in paths.OCR_FILES if paths.ocr_exists(f)]
texts = {}
for f in files:
    for line in paths.read_ocr_lines(f):
        r = json.loads(line)
        texts.setdefault(r["product_id"], []).append(r["text"])

prod = pd.read_csv(paths.PRODUCTS, dtype={"product_id": str})
prod = prod[prod["allergy_info"].notna() & prod["product_id"].isin(texts)]
keys = ("함유", "같은시설", "함유+원재료명")
stats = {k: {"tp": 0, "fp": 0, "fn": 0, "exact": 0, "n": 0} for k in keys}
for _, r in prod.iterrows():
    truth = backend_extract(None, r["allergy_info"])
    got = E.extract(texts[r["product_id"]])
    both = ", ".join(x for x in (got["함유_알레르기"], got["원재료명_알레르겐"]) if x)
    for key, t, g in (("함유", truth["contains"], got["함유_알레르기"]), ("같은시설", truth["cross"], got["같은시설_알레르기"]),
                      ("함유+원재료명", truth["contains"], both)):
        t, g = set(t), {x for x in g.split(", ") if x}
        if not t and not g:
            continue
        s = stats[key]
        s["n"] += 1
        s["tp"] += len(t & g)
        s["fp"] += len(g - t)
        s["fn"] += len(t - g)
        s["exact"] += t == g
for k, s in stats.items():
    p = s["tp"] / max(1, s["tp"] + s["fp"])
    rc = s["tp"] / max(1, s["tp"] + s["fn"])
    print(f"{k}: 상품 {s['n']}  정밀도 {p:.3f}  재현율 {rc:.3f}  완전일치 {s['exact'] / max(1, s['n']):.3f}")
