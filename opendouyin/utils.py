import json
import logging
import re
import shutil
import subprocess
import wave
from pathlib import Path

log = logging.getLogger("opendouyin")

_ffmpeg: str | None = None


def ffmpeg_bin() -> str:
    """ffmpeg trên PATH, nếu không có thì dùng bản đóng gói của imageio-ffmpeg."""
    global _ffmpeg
    if _ffmpeg:
        return _ffmpeg
    exe = shutil.which("ffmpeg")
    if not exe:
        try:
            import imageio_ffmpeg

            exe = imageio_ffmpeg.get_ffmpeg_exe()
        except ImportError as e:
            raise RuntimeError(
                "Không tìm thấy ffmpeg. Cài bằng: winget install Gyan.FFmpeg"
            ) from e
    _ffmpeg = exe
    return exe


def run_ffmpeg(args, cwd: Path | None = None) -> subprocess.CompletedProcess:
    cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y", *map(str, args)]
    log.debug("ffmpeg %s", " ".join(cmd[1:]))
    proc = subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg lỗi:\n{proc.stderr[-3000:]}")
    return proc


_DURATION = re.compile(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)")
_VIDEO = re.compile(r"Stream #.*Video:.*?(\d{2,5})x(\d{2,5})")


def media_info(path: Path) -> dict:
    """Đọc thời lượng / kích thước / có audio hay không từ `ffmpeg -i` (không cần ffprobe)."""
    proc = subprocess.run(
        [ffmpeg_bin(), "-hide_banner", "-i", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    out = proc.stderr
    info = {"duration": 0.0, "width": 0, "height": 0, "has_audio": "Audio:" in out}
    if m := _DURATION.search(out):
        h, mi, s = m.groups()
        info["duration"] = int(h) * 3600 + int(mi) * 60 + float(s)
    if m := _VIDEO.search(out):
        info["width"], info["height"] = int(m.group(1)), int(m.group(2))
    return info


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: Path, data) -> None:
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def fmt_srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(path: Path, cues: list[tuple[float, float, str]]) -> None:
    lines = []
    for i, (start, end, text) in enumerate(cues, 1):
        lines += [str(i), f"{fmt_srt_time(start)} --> {fmt_srt_time(end)}", text, ""]
    Path(path).write_text("\n".join(lines), encoding="utf-8")
