"""RapidOCR(PaddleOCR PP-OCRv5 한국어 모델)로 표시사항 이미지 읽기. 그래픽카드(DirectML)를 쓴다.

    python ocr_rapid.py                  # 원재료 상품을 뺀 가공품 이미지 → ocr_output/ocr_rapid.jsonl
    python ocr_rapid.py --targets all    # 전체 이미지

Windows OCR보다 느리지만(초당 약 3~4장, RTX 5060 기준) 색 박스 안의 '우유 함유' 같은 굵은 표시와
작은 글씨를 훨씬 잘 읽는다 (표본 120개: 함유 알레르기 재현율 Windows 41% → RapidOCR 76%).

글자 줄마다 위치(상자 좌표)도 저장한다 → extract_label.py 가 표 안의 '원재료명' 칸만 골라 읽는 데 쓴다.
한 줄 = {"t": 글자, "b": [x0, y0, x1, y1] (불러온 이미지 기준 픽셀), "s": 신뢰도}
"""
import argparse

import imgutil
import jobs
import paths

TILE_H, OVERLAP = 2000, 150
_engine = None


def _get():
    global _engine
    if _engine is None:
        from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR
        _engine = RapidOCR(params={
            "Rec.lang_type": LangRec.KOREAN, "Rec.ocr_version": OCRVersion.PPOCRV5, "Rec.model_type": ModelType.MOBILE,
            "EngineConfig.onnxruntime.use_dml": True,   # 그래픽카드 (onnxruntime-directml). 없으면 CPU로 돈다
            "Global.log_level": "error",
        })
    return _engine


def work(file):
    import numpy as np
    try:
        # 흑백 1배 (폭 1000~1600). 표본 실험에서 컬러보다 조금 낫고, 2배는 느린 데 비해 이득이 없었다
        img = imgutil.load(paths.BASE / file, 1, min_w=1000, max_w=1600, gray=True)
        lines, top = [], 0
        for tile in imgutil.tiles(img, TILE_H, OVERLAP):
            r = _get()(np.array(tile))
            if r.txts:
                for box, t, s in zip(r.boxes, r.txts, r.scores):
                    xs, ys = [p[0] for p in box], [p[1] for p in box]
                    lines.append({"t": t, "b": [int(min(xs)), int(min(ys) + top), int(max(xs)), int(max(ys) + top)],
                                  "s": round(float(s), 3)})
            top += TILE_H - OVERLAP
        return {"text": "\n".join(l["t"] for l in lines), "lines": lines, "size": list(img.size), "error": None}
    except Exception as e:
        return {"text": "", "lines": [], "error": f"{type(e).__name__}: {e}"}


def _read(img):
    import numpy as np
    lines, top = [], 0
    for tile in imgutil.tiles(img, TILE_H, OVERLAP):
        r = _get()(np.array(tile))
        if r.txts:
            for box, t, s in zip(r.boxes, r.txts, r.scores):
                xs, ys = [p[0] for p in box], [p[1] for p in box]
                lines.append({"t": t, "b": [int(min(xs)), int(min(ys) + top), int(max(xs)), int(max(ys) + top)],
                              "s": round(float(s), 3)})
        top += TILE_H - OVERLAP
    return lines


def work_extra(file):
    """보완 판독 (3가지가 하나도 안 보인 상품만): 세로로 돌아간 표시사항(90°·270°)과 연한 글씨(CLAHE 대비 보정).
    세 변형의 글자를 모두 합쳐 저장하고, 좌표는 3가지 문구가 가장 많이 보인 변형의 것을 남긴다."""
    import cv2
    import numpy as np
    from PIL import Image

    import classify
    try:
        base = imgutil.load(paths.BASE / file, 1, min_w=1000, max_w=1600, gray=True)
        g = np.array(base.convert("L"))
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(g)
        variants = {"rot90": base.rotate(90, expand=True), "rot270": base.rotate(270, expand=True),
                    "clahe": Image.fromarray(clahe).convert("RGB")}
        read = {name: _read(img) for name, img in variants.items()}
        texts = {name: "\n".join(l["t"] for l in lines) for name, lines in read.items()}
        best = max(read, key=lambda n: (sum(classify.flags(texts[n]).values()), len(texts[n])))
        return {"text": "\n".join(texts.values()), "lines": read[best], "variant": best, "error": None}
    except Exception as e:
        return {"text": "", "lines": [], "error": f"{type(e).__name__}: {e}"}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--targets", default="processed", choices=["processed", "all"])
    ap.add_argument("--workers", type=int, default=3)   # 그래픽카드 하나에 3개가 가장 빨랐다
    ap.add_argument("--extra", action="store_true",
                    help="보완 판독: result/label_ocr_missing.csv 의 상품만 회전·대비 보정해서 다시 읽기")
    a = ap.parse_args()
    if a.extra:
        import pandas as pd
        missing = pd.read_csv(paths.RESULT_DIR / "label_ocr_missing.csv", dtype={"product_id": str})["product_id"]
        todo = jobs.targets("all")
        jobs.run(work_extra, todo[todo["product_id"].isin(missing)], paths.OCR_RAPID_EXTRA, a.workers, report_every=50)
    else:
        jobs.run(work, jobs.targets(a.targets), paths.OCR_RAPID, a.workers)
