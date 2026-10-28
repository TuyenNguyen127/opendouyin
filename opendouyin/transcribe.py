"""Bước 3a — Lấy sub (thay cho TurboScribe): Whisper local, fallback Claude đọc khung hình."""
import os
import sys
import wave
from pathlib import Path

from pydantic import BaseModel

from . import llm
from .utils import log, media_info, run_ffmpeg, write_json, write_srt


def _load_audio(video: Path, work: Path):
    """Tự giải mã sang 16 kHz mono bằng ffmpeg (decoder PyAV của faster-whisper lệch API với av>=19)."""
    import numpy as np

    wav = work / "build" / "audio16k.wav"
    wav.parent.mkdir(exist_ok=True)
    run_ffmpeg(["-i", video, "-vn", "-ac", 1, "-ar", 16000, "-c:a", "pcm_s16le", wav])
    with wave.open(str(wav), "rb") as w:
        pcm = w.readframes(w.getnframes())
    return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0


def _whisper(audio, c, device: str) -> tuple[list[dict], str]:
    from faster_whisper import WhisperModel

    compute = c.compute_type if c.compute_type != "auto" else ("float16" if device == "cuda" else "int8")
    log.info("Whisper %s trên %s (%s)", c.model, device, compute)
    model = WhisperModel(c.model, device=device, compute_type=compute)
    segments, info = model.transcribe(
        audio,
        language=c.language or None,
        vad_filter=True,
        beam_size=5,
        word_timestamps=True,
        initial_prompt="以下是普通话的句子，带标点。" if c.language == "zh" else None,
    )
    out = []
    for s in segments:  # generator: lỗi CUDA (thiếu cuDNN...) thường nổ ở đây
        for start, end, text in _split(s):
            out.append({"id": len(out), "start": round(start, 2), "end": round(end, 2), "text": text})
    return out, info.language


_SENTENCE_END = tuple("。！？!?…")
_CLAUSE_END = tuple("，,；;：:")


def _split(seg, max_len: float = 4.0):
    """Cắt đoạn Whisper dài thành câu ngắn theo dấu câu → khớp hình với giọng mới chính xác hơn."""
    words = seg.words or []
    if not words:
        if seg.text.strip():
            yield seg.start, seg.end, seg.text.strip()
        return
    buf, start = "", words[0].start
    for i, w in enumerate(words):
        buf += w.word
        token = w.word.strip()
        last = i == len(words) - 1
        if last or token.endswith(_SENTENCE_END) or (token.endswith(_CLAUSE_END) and w.end - start >= max_len):
            if buf.strip():
                yield start, w.end, buf.strip()
            buf = ""
            if not last:
                start = words[i + 1].start


def _register_cuda_dlls() -> None:
    """Windows: thêm DLL của gói pip nvidia-cublas-cu12 / nvidia-cudnn-cu12 vào đường tìm kiếm."""
    if os.name != "nt":
        return
    for base in map(Path, sys.path):
        for bin_dir in (base / "nvidia").glob("*/bin"):
            os.add_dll_directory(str(bin_dir))
            os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"


def _device(c) -> str:
    _register_cuda_dlls()
    if c.device != "auto":
        return c.device
    try:
        import ctranslate2

        return "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
    except Exception:
        return "cpu"


class _Cue(BaseModel):
    start: float
    end: float
    text: str


class _VisionTranscript(BaseModel):
    cues: list[_Cue]


def _vision(video: Path, work: Path, cfg, duration: float) -> list[dict]:
    """Video chỉ có nhạc/hardsub: trích khung hình, nhờ Claude đọc chữ & mô tả diễn biến."""
    c = cfg.transcribe
    frames_dir = work / "frames"
    frames_dir.mkdir(exist_ok=True)
    step = max(c.vision_interval, duration / c.vision_max_frames)
    content: list = []
    t = 0.5
    while t < duration:
        f = frames_dir / f"{t:07.2f}.jpg"
        if not f.exists():
            run_ffmpeg(["-ss", f"{t:.2f}", "-i", video, "-frames:v", "1", "-vf", "scale=512:-2", "-q:v", "4", f])
        content += [{"type": "text", "text": f"t={t:.1f}s"}, llm.image_block(f)]
        t += step
    content.append({"type": "text", "text": (
        f"Video dài {duration:.1f}s. Dựa vào các khung hình trên (kèm mốc thời gian), hãy dựng lại "
        "lời thoại/nội dung theo thời gian: nếu có phụ đề in sẵn thì chép lại nguyên văn, nếu không "
        "thì mô tả ngắn diễn biến. Mỗi cue 2–8 giây, không chồng thời gian."
    )})
    log.info("Không có thoại — gửi %d khung hình cho Claude đọc", len(content) // 2)
    result = llm.ask(
        cfg, "vision", work,
        "Bạn là chuyên viên làm phụ đề, đọc chữ trên hình (OCR) và tóm tắt diễn biến video.",
        content, _VisionTranscript,
    )
    return [{"id": i, "start": round(q.start, 2), "end": round(q.end, 2), "text": q.text}
            for i, q in enumerate(sorted(result.cues, key=lambda q: q.start))]


def transcribe(video: Path, work: Path, cfg) -> dict:
    c = cfg.transcribe
    duration = media_info(video)["duration"]
    device = _device(c)
    audio = _load_audio(video, work)
    try:
        segments, lang = _whisper(audio, c, device)
    except Exception as e:
        if device != "cuda":
            raise
        log.warning("Whisper GPU lỗi (%s) — chạy lại bằng CPU", e)
        segments, lang = _whisper(audio, c, "cpu")
    source = "whisper"

    if len(segments) < 3 and c.vision_fallback:
        if llm.supports_images(cfg):
            segments, source = _vision(video, work, cfg, duration), "vision"
        else:
            log.warning("Video gần như không có thoại. Đọc hardsub từ hình cần llm.provider: claude — "
                        "hoặc tự điền transcript.json rồi chạy resume --from rewrite")

    data = {"source": source, "language": lang, "duration": duration, "segments": segments}
    write_json(work / "transcript.json", data)
    write_srt(work / "transcript.srt", [(s["start"], s["end"], s["text"]) for s in segments])
    log.info("Transcript: %d đoạn (%s)", len(segments), source)
    return data
