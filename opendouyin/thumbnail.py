"""Bước 6b — Thumbnail (thay cho Canva): khung hình cao trào + chữ lớn viền đen."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

from .utils import log, read_json, run_ffmpeg


def _font(paths: list[str], size: int):
    for p in paths:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    log.warning("Không tìm thấy font trong thumbnail.fonts — dùng font mặc định")
    return ImageFont.load_default(size)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if draw.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    return lines + [cur] if cur else lines


def _pick_time(work: Path) -> float:
    """Giữa đoạn hook trong video gốc; không có thì 1/3 video."""
    try:
        script = read_json(work / "script.json")
        transcript = read_json(work / "transcript.json")
        segs = {s["id"]: s for s in transcript["segments"]}
        ids = script["hook"]["source_ids"]
        return (segs[ids[0]]["start"] + segs[ids[-1]]["end"]) / 2
    except Exception:
        return read_json(work / "transcript.json")["duration"] / 3


def _shorten(text: str, max_words: int = 8) -> str:
    """Model nhỏ hay viết chữ thumbnail quá dài: lấy vế đầu, rồi cắt theo số từ."""
    for sep in (":", ",", "."):
        if len(text.split()) > max_words and sep in text:
            text = text.split(sep)[0]
    return " ".join(text.split()[:max_words])


def thumbnail(work: Path, text: str, cfg) -> Path:
    text = _shorten(text)
    e = cfg.edit
    vertical = e.aspect != "16:9"
    W, H = (1080, 1920) if vertical else (1280, 720)
    frame = work / "build" / "thumb_frame.jpg"
    frame.parent.mkdir(exist_ok=True)
    run_ffmpeg(["-ss", f"{_pick_time(work):.2f}", "-i", work / "source.mp4", "-frames:v", 1, "-q:v", 2, frame])

    img = Image.open(frame).convert("RGB")
    if e.mirror:
        img = img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    scale = max(W / img.width, H / img.height)  # cover-crop
    img = img.resize((round(img.width * scale), round(img.height * scale)), Image.Resampling.LANCZOS)
    left, top = (img.width - W) // 2, (img.height - H) // 2
    img = img.crop((left, top, left + W, top + H))
    img = ImageEnhance.Contrast(ImageEnhance.Color(img).enhance(1.3)).enhance(1.15)

    # gradient tối ở nửa dưới để chữ nổi
    shade = Image.new("L", (1, H))
    for y in range(H):
        shade.putpixel((0, y), int(200 * max(0, (y - H * 0.45) / (H * 0.55))))
    img = Image.composite(Image.new("RGB", (W, H), "black"), img, shade.resize((W, H)))

    draw = ImageDraw.Draw(img)
    size = W // 9
    font = _font(cfg.thumbnail.fonts, size)
    lines = _wrap(draw, text.upper(), font, int(W * 0.9))
    while len(lines) > 3 and size > W // 18:
        size = int(size * 0.88)
        font = _font(cfg.thumbnail.fonts, size)
        lines = _wrap(draw, text.upper(), font, int(W * 0.9))

    line_h = int(size * 1.2)
    y = int(H * 0.88) - line_h * len(lines)
    for i, line in enumerate(lines):
        w = draw.textlength(line, font=font)
        draw.text(((W - w) / 2, y), line, font=font, fill="#FFE600" if i == 0 else "white",
                  stroke_width=max(4, size // 12), stroke_fill="black")
        y += line_h

    out = work / "thumbnail.jpg"
    img.save(out, quality=92)
    log.info("Thumbnail: %s", out)
    return out
