"""이미지 불러오기와 긴 이미지 자르기 (OCR 엔진과 무관)."""
from PIL import Image, ImageSequence

Image.MAX_IMAGE_PIXELS = None   # 상세 이미지는 세로 4만 px 짜리도 있다


def load(path, scale=1.0, min_w=0, max_w=2000, gray=False) -> Image.Image:
    """움직이는 GIF는 첫 장면만. scale배로 키우되 폭이 min_w보다 작으면 min_w까지, max_w를 넘지 않게 맞춘다
    (LANCZOS 보간). gray=True면 흑백으로 바꾼다.

    10/08 표본 120개 실험: 흑백 + 확대는 도움이 되고, 이진화(Otsu·적응형)는 두 엔진 모두 오히려 나빠졌다
    (색 박스 안의 흰 글씨 '우유 함유' 같은 표시가 지워진다). 그래서 이진화는 하지 않는다.
    """
    img = Image.open(path)
    if getattr(img, "is_animated", False):
        img = next(ImageSequence.Iterator(img))
    img = img.convert("L" if gray else "RGB")
    w, h = img.size
    s = scale
    if w * s < min_w:
        s = min_w / w
    if w * s > max_w:
        s = max_w / w
    if abs(s - 1) > 1e-3:
        img = img.resize((round(w * s), round(h * s)), Image.LANCZOS)
    return img.convert("RGB")


def tiles(img: Image.Image, height: int, overlap: int):
    """세로로 긴 이미지를 겹치게 잘라 차례로 준다 (경계에 걸친 줄을 놓치지 않게)."""
    w, h = img.size
    top = 0
    while top < h:
        bottom = min(h, top + height)
        yield img.crop((0, top, w, bottom))
        if bottom == h:
            break
        top = bottom - overlap
