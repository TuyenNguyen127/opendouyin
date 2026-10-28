"""Bước 2 — Tải video gốc (thay cho SnapAny) bằng yt-dlp, hoặc nhận file local."""
import os
import re
import shutil
import time
from pathlib import Path

from .utils import ffmpeg_bin, log, run_ffmpeg


def project_id(source: str) -> str:
    """Tên thư mục project suy ra từ link: BVxxxx, id Douyin/TikTok/YouTube, hoặc tên file."""
    p = Path(source)
    if p.exists():
        return re.sub(r"[^\w\-]+", "_", p.stem)[:60]
    for pattern in (r"(BV[0-9A-Za-z]{10})", r"/video/(\d{8,})", r"modal_id=(\d{8,})",
                    r"[?&]v=([\w-]{11})", r"youtu\.be/([\w-]{11})", r"/shorts/([\w-]{11})"):
        if m := re.search(pattern, source):
            return m.group(1)
    return time.strftime("clip_%Y%m%d_%H%M%S")


def download(source: str, out_dir: Path, cfg) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "source.mp4"

    local = Path(source)
    if local.exists():
        if local.suffix.lower() == ".mp4":
            shutil.copy2(local, target)
        else:
            run_ffmpeg(["-i", local, "-c", "copy", target])
        return {"source": str(local.resolve()), "title": local.stem, "extractor": "local"}

    if m := re.search(r"(BV[0-9A-Za-z]{10})", source):
        p = re.search(r"[?&]p=(\d+)", source)
        return _bilibili(m.group(1), int(p.group(1)) if p else 1, out_dir, target)

    import yt_dlp

    opts = {
        "format": cfg.download.format,
        "outtmpl": str(out_dir / "source.%(ext)s"),
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "ffmpeg_location": ffmpeg_bin(),
    }
    if cfg.download.cookies:
        opts["cookiefile"] = cfg.download.cookies
    log.info("Đang tải %s", source)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(source, download=True)

    if not target.exists():
        got = sorted(out_dir.glob("source.*"))
        if not got:
            raise RuntimeError("yt-dlp không tạo ra file video")
        run_ffmpeg(["-i", got[0], "-c", "copy", target])
        got[0].unlink()

    return {
        "source": source,
        "title": info.get("title", ""),
        "uploader": info.get("uploader", ""),
        "description": (info.get("description") or "")[:2000],
        "duration": info.get("duration"),
        "views": info.get("view_count"),
        "likes": info.get("like_count"),
        "extractor": info.get("extractor_key", ""),
    }


def _bilibili(bvid: str, page: int, out_dir: Path, target: Path) -> dict:
    """Trang web Bilibili chặn yt-dlp (HTTP 412) nên tải thẳng qua API playurl.
    Khách: tối đa 480p. Đặt BILIBILI_SESSDATA trong .env (cookie đăng nhập) để lấy 1080p."""
    from .discover import Bilibili

    bili = Bilibili()
    if sess := os.environ.get("BILIBILI_SESSDATA"):
        bili.s.cookies.set("SESSDATA", sess, domain=".bilibili.com")
    view = bili._get("/x/web-interface/wbi/view", {"bvid": bvid}, wbi=True)
    pages = view.get("pages") or [{"cid": view["cid"]}]
    cid = pages[min(page, len(pages)) - 1]["cid"]
    play = bili._get("/x/player/wbi/playurl",
                     {"bvid": bvid, "cid": cid, "qn": 80, "fnval": 16, "fourk": 1}, wbi=True)

    def fetch(url: str, dest: Path):
        with bili.s.get(url, stream=True, timeout=30) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)

    dash = play.get("dash")
    if dash:
        # ưu tiên H.264 (avc) cho tương thích, chất lượng cao nhất trong cùng codec
        videos = sorted(dash["video"], key=lambda x: (x["codecs"].startswith("avc"), x["id"], x.get("bandwidth", 0)))
        video = videos[-1]
        audio = max(dash.get("audio") or [], key=lambda x: x.get("bandwidth", 0), default=None)
        log.info("Bilibili %s: %dp %s", bvid, video["height"], video["codecs"])
        vtmp, atmp = out_dir / "video.m4s", out_dir / "audio.m4s"
        fetch(video["baseUrl"], vtmp)
        inputs = ["-i", vtmp]
        if audio:
            fetch(audio["baseUrl"], atmp)
            inputs += ["-i", atmp]
        run_ffmpeg([*inputs, "-c", "copy", target])
        vtmp.unlink()
        atmp.unlink(missing_ok=True)
    else:
        tmp = out_dir / "source.flv"
        fetch(play["durl"][0]["url"], tmp)
        run_ffmpeg(["-i", tmp, "-c", "copy", target])
        tmp.unlink()

    st = view.get("stat", {})
    return {
        "source": f"https://www.bilibili.com/video/{bvid}",
        "title": view.get("title", ""),
        "uploader": view.get("owner", {}).get("name", ""),
        "description": (view.get("desc") or "")[:2000],
        "duration": view.get("duration"),
        "views": st.get("view"),
        "likes": st.get("like"),
        "extractor": "BiliBili",
    }
