"""Windows 내장 OCR(한국어)로 표시사항 이미지 읽기. 빠르다 (CPU, 초당 약 10장).

    python ocr_windows.py                                 # 전체 이미지, 컬러 1배 → ocr_output/ocr_windows.jsonl
    python ocr_windows.py --scale 2 --targets processed   # 가공품, 흑백 2배 → ocr_output/ocr_windows_2x.jsonl

흑백 2배는 작은 글씨를 보완한다 (표본 120개: 함유 알레르기 재현율 41% → 56%).

Windows 설정에 한국어 OCR이 있어야 한다 (한국어 Windows는 기본으로 있음).
같은 프로세스에서 RapidOCR(onnxruntime)과 함께 불러오면 충돌하므로 따로 실행한다.
"""
import argparse
import asyncio

import imgutil
import jobs
import paths

TILE_H, OVERLAP = 2400, 200
SCALE = 1


def _engine():
    import winrt.windows.globalization as glob
    import winrt.windows.media.ocr as ocr
    return ocr.OcrEngine.try_create_from_language(glob.Language("ko"))


async def _read(engine, img) -> str:
    import winrt.windows.graphics.imaging as imaging
    import winrt.windows.storage.streams as streams
    rgba = img.convert("RGBA")
    writer = streams.DataWriter()
    writer.write_bytes(rgba.tobytes())
    bitmap = imaging.SoftwareBitmap.create_copy_from_buffer(
        writer.detach_buffer(), imaging.BitmapPixelFormat.RGBA8, rgba.width, rgba.height)
    bitmap = imaging.SoftwareBitmap.convert(bitmap, imaging.BitmapPixelFormat.BGRA8)
    result = await engine.recognize_async(bitmap)
    return "\n".join(line.text for line in result.lines)


async def _ocr(path, scale) -> str:
    engine = _engine()
    if scale == 1:
        img = imgutil.load(path, 1, min_w=1000, max_w=2000)
    else:
        img = imgutil.load(path, scale, max_w=2400, gray=True)
    return "\n".join([await _read(engine, tile) for tile in imgutil.tiles(img, TILE_H, OVERLAP)])


def work(file):
    try:
        return {"text": asyncio.run(_ocr(paths.BASE / file, SCALE)), "error": None}
    except Exception as e:
        return {"text": "", "error": f"{type(e).__name__}: {e}"}


def _init(scale):
    global SCALE
    SCALE = scale


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--scale", type=int, default=1, choices=[1, 2])
    ap.add_argument("--targets", default="all", choices=["all", "processed"])
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    out = paths.OCR_WINDOWS if a.scale == 1 else paths.OCR_WINDOWS_2X
    jobs.run(work, jobs.targets(a.targets), out, a.workers, report_every=500, init=_init, initargs=(a.scale,))
