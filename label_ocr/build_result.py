"""OCR 결과(ocr_output/*.jsonl) → 상품별 표시사항 판정·추출 표.

    python build_result.py

결과 (result/):
- label_ocr_result.csv   : 상품 전체. 3가지 유무 + 원재료명 · 함유 알레르기 · 같은 시설 알레르기 · 근거 문장
- label_ocr_missing.csv  : 원재료 상품을 뺀 가공품 중 3가지가 모두 안 보이는 상품
- label_ocr_result.xlsx  : 요약 + 위 두 표 (엑셀로 보기용)

OCR 판독 여러 개(Windows 1배 · 2배, RapidOCR)가 있으면 모두 합쳐서 찾는다 (엔진마다 놓치는 곳이 달라서).
"""
import json

import pandas as pd

import classify
import extract_label as E
import paths


def load_ocr():
    """상품별 OCR 글자 [판독·이미지마다], RapidOCR 줄 좌표 [이미지마다]"""
    texts, lines = {}, {}
    for f in paths.OCR_FILES:
        if paths.ocr_exists(f):
            for line in paths.read_ocr_lines(f):
                if line.strip():
                    r = json.loads(line)
                    texts.setdefault(r["product_id"], []).append(r["text"])
                    if r.get("lines"):
                        lines.setdefault(r["product_id"], []).append(r["lines"])
    return texts, lines


def main():
    prod = pd.read_csv(paths.PRODUCTS, dtype={"product_id": str})
    img = pd.read_csv(paths.LABEL_TABLE, dtype={"product_id": str})
    texts, lines = load_ocr()

    per_img = img.groupby("product_id")
    out = prod[["product_id", "product_name", "category", "subcategory"]].rename(
        columns={"product_name": "상품명", "category": "카테고리", "subcategory": "소분류"})
    out["원재료상품"] = [classify.is_raw(c, s, n) for c, s, n in zip(prod["category"], prod["subcategory"], prod["product_name"])]
    out["표시사항출처"] = out["product_id"].map(per_img["label_source"].first())
    out["이미지수"] = out["product_id"].map(per_img.size()).fillna(0).astype(int)

    ext = pd.DataFrame([E.extract(texts.get(pid, []), lines.get(pid)) for pid in out["product_id"]], index=out.index)
    flg = pd.DataFrame([classify.flags("\n".join(texts.get(pid, []))) for pid in out["product_id"]], index=out.index)
    # 문구가 보이거나(키워드) 내용을 뽑았으면 '있음'
    out["원재료명_있음"] = flg["원재료명"] | ext["원재료명"].str.len().gt(0)
    out["알레르기함유_있음"] = flg["알레르기함유"] | ext["함유_알레르기"].str.len().gt(0)
    out["같은시설_있음"] = flg["같은시설"] | ext["같은시설_알레르기"].str.len().gt(0)
    out["3가지중_있음"] = out[["원재료명_있음", "알레르기함유_있음", "같은시설_있음"]].sum(axis=1)
    out = pd.concat([out, ext.rename(columns={"원재료명": "원재료명_내용"})], axis=1)
    out["이미지폴더"] = "label_images\\" + out["product_id"]

    target = out[~out["원재료상품"] & (out["이미지수"] > 0)]
    missing = target[target["3가지중_있음"] == 0].copy()
    # 생수·얼음·탄산수는 원재료명 대신 '원수원'을 적고 알레르기 표시 대상이 아니라 3가지가 없는 게 정상이다.
    # 나머지는 단일 원료(건대추·꿀·소금 등)라 실제로 없는 것과, OCR이 못 읽은 것(작은 이미지·세로 글씨·
    # 아주 긴 상세 이미지 속 표시사항)이 섞여 있어 사람이 이미지를 봐야 한다.
    water = missing["소분류"].isin(["생수/얼음", "탄산수"]) | missing["상품명"].str.contains(
        "생수|탄산수|얼음|미네랄워터|광천수|해양심층수", na=False)
    missing.insert(missing.columns.get_loc("3가지중_있음") + 1, "비고",
                   water.map({True: "생수·얼음 (원재료·알레르기 표시 대상 아님)", False: "이미지 확인 필요"}))
    summary = pd.DataFrame([
        ("전체 상품", len(out)),
        ("표시사항 이미지 없음 (판매 종료 등)", int((out["이미지수"] == 0).sum())),
        ("원재료 상품 (제외)", int((out["원재료상품"] & (out["이미지수"] > 0)).sum())),
        ("판정 대상 가공품", len(target)),
        ("  원재료명 있음", int(target["원재료명_있음"].sum())),
        ("  알레르기 함유 표시 있음", int(target["알레르기함유_있음"].sum())),
        ("  같은 시설 제조 표시 있음", int(target["같은시설_있음"].sum())),
        ("  3가지 모두 있음", int((target["3가지중_있음"] == 3).sum())),
        ("  2가지", int((target["3가지중_있음"] == 2).sum())),
        ("  1가지", int((target["3가지중_있음"] == 1).sum())),
        ("  3가지 모두 없음", len(missing)),
        ("    그중 생수·얼음·탄산수", int(water.sum())),
        ("    그중 이미지 확인 필요", int((~water).sum())),
    ], columns=["항목", "상품 수"])

    paths.RESULT_DIR.mkdir(parents=True, exist_ok=True)
    out.to_csv(paths.RESULT_DIR / "label_ocr_result.csv", index=False, encoding="utf-8-sig")
    missing.to_csv(paths.RESULT_DIR / "label_ocr_missing.csv", index=False, encoding="utf-8-sig")
    with pd.ExcelWriter(paths.RESULT_DIR / "label_ocr_result.xlsx") as xw:
        summary.to_excel(xw, sheet_name="요약", index=False)
        missing.to_excel(xw, sheet_name="3가지 모두 없음", index=False)
        out.to_excel(xw, sheet_name="전체 결과", index=False)
    print(summary.to_string(index=False))
    print("→", paths.RESULT_DIR)
    return out, missing


if __name__ == "__main__":
    main()
