"""Bước 4 — Tạo giọng đọc mới cho từng câu (edge-tts miễn phí hoặc ElevenLabs)."""
import asyncio
import hashlib
import os
import time
from pathlib import Path

import requests

from .utils import log, run_ffmpeg, wav_duration


def _edge(text: str, mp3: Path, c) -> None:
    import edge_tts

    asyncio.run(edge_tts.Communicate(text, c.edge_voice, rate=c.edge_rate).save(str(mp3)))


def _elevenlabs(text: str, mp3: Path, c) -> None:
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key or not c.elevenlabs_voice_id:
        raise RuntimeError("Cần ELEVENLABS_API_KEY trong .env và tts.elevenlabs_voice_id trong config")
    r = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{c.elevenlabs_voice_id}",
        params={"output_format": "mp3_44100_128"},
        headers={"xi-api-key": key},
        json={"text": text, "model_id": c.elevenlabs_model,
              "voice_settings": {"stability": 0.45, "similarity_boost": 0.8, "style": 0.3}},
        timeout=120,
    )
    if r.status_code != 200:
        raise RuntimeError(f"ElevenLabs lỗi {r.status_code}: {r.text[:300]}")
    mp3.write_bytes(r.content)


def synth_one(text: str, out_dir: Path, name: str, c) -> Path:
    """Tên file chứa hash nội dung → sửa script.json thì chỉ câu bị sửa được đọc lại."""
    h = hashlib.sha1(f"{c.provider}|{c.edge_voice}|{c.edge_rate}|{c.elevenlabs_voice_id}|{text}".encode()).hexdigest()[:8]
    wav = out_dir / f"{name}_{h}.wav"
    if wav.exists():
        return wav
    mp3 = wav.with_suffix(".mp3")
    engine = _elevenlabs if c.provider == "elevenlabs" else _edge
    for attempt in range(4):  # edge-tts thỉnh thoảng trả "No audio was received" khi gọi dồn dập
        try:
            engine(text, mp3, c)
            break
        except Exception as e:
            mp3.unlink(missing_ok=True)
            if attempt == 3:
                raise RuntimeError(f"TTS lỗi ở câu {name}: {e}\n  {text}") from e
            log.warning("TTS câu %s lỗi (%s), thử lại...", name, e)
            time.sleep(2 * (attempt + 1))
    run_ffmpeg(["-i", mp3, "-ar", 48000, "-ac", 2, wav])
    mp3.unlink(missing_ok=True)
    return wav


def synthesize(script: dict, work: Path, cfg) -> dict:
    c = cfg.tts
    out_dir = work / "voice"
    out_dir.mkdir(exist_ok=True)
    result = {"hook": None, "lines": []}
    if script.get("hook", {}).get("text"):
        wav = synth_one(script["hook"]["text"], out_dir, "hook", c)
        result["hook"] = {"file": wav.name, "duration": round(wav_duration(wav), 3)}
    for i, line in enumerate(script["lines"]):
        wav = synth_one(line["text"], out_dir, f"{i:03d}", c)
        result["lines"].append({"file": wav.name, "duration": round(wav_duration(wav), 3)})
        log.debug("TTS %03d: %.2fs", i, result["lines"][-1]["duration"])
    total = sum(l["duration"] for l in result["lines"])
    log.info("TTS xong %d câu, tổng %.1fs giọng đọc (%s)", len(result["lines"]), total, c.provider)
    return result
