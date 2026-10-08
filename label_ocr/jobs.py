"""OCR 실행 공통: 대상 이미지 고르기, 여러 프로세스로 돌리기, 이어서 하기."""
import json
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

import pandas as pd

import paths


def targets(which: str = "processed") -> pd.DataFrame:
    """OCR할 이미지 [product_id, file]. which='processed'면 원재료 상품을 뺀 가공품만, 'all'이면 전부."""
    img = pd.read_csv(paths.LABEL_TABLE, dtype={"product_id": str}).dropna(subset=["file"])
    if which == "processed":
        from classify import raw_product_ids
        img = img[~img["product_id"].isin(raw_product_ids())]
    return img[["product_id", "file"]]


def run(work, todo: pd.DataFrame, out_path, workers: int, report_every: int = 200, init=None, initargs=()):
    """work(file) → {"text": …, "error": …, (그 밖의 값)} 을 여러 프로세스로 돌려 out_path(JSONL)에 한 줄씩 쌓는다.
    이미 쓴 이미지는 건너뛰므로 끊겨도 다시 실행하면 이어서 한다.
    init(*initargs)는 자식 프로세스마다 한 번 실행된다 (Windows는 spawn이라 전역 설정이 이어지지 않음)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not out_path.exists() and paths.ocr_exists(out_path):   # 저장소에서 받은 압축본(.jsonl.gz)이면 풀어서 이어서 한다
        out_path.write_text("\n".join(paths.read_ocr_lines(out_path)) + "\n", encoding="utf-8")
    done = set()
    if out_path.exists():
        for line in out_path.read_text(encoding="utf-8").splitlines():
            try:
                done.add(json.loads(line)["file"])
            except (json.JSONDecodeError, KeyError):
                pass
    todo = todo[~todo["file"].isin(done)]
    print(f"대상 {len(todo) + len(done)}장 / 이미 함 {len(done)} / 이번 {len(todo)}", flush=True)
    t0, n = time.time(), 0
    with ProcessPoolExecutor(workers, initializer=init, initargs=initargs) as pool, open(out_path, "a", encoding="utf-8") as fh:
        futures = {pool.submit(work, f): (pid, f) for pid, f in zip(todo["product_id"], todo["file"])}
        for fut in as_completed(futures):
            pid, file = futures[fut]
            fh.write(json.dumps({"product_id": pid, "file": file, **fut.result()}, ensure_ascii=False) + "\n")
            fh.flush()
            n += 1
            if n % report_every == 0:
                rate = n / (time.time() - t0)
                print(f"{n}/{len(todo)}  {rate:.1f}장/초  남은 약 {(len(todo) - n) / rate / 60:.0f}분", flush=True)
    print(f"끝: {n}장, {(time.time() - t0) / 60:.1f}분 → {out_path}", flush=True)
