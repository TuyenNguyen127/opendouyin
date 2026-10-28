"""Bước 5 — Dựng lại video (thay cho CapCut thủ công).

Timeline gồm các block cắt từ video gốc:
  hook   : cảnh cao trào đưa lên đầu + câu teaser
  speech : cảnh của các đoạn thoại gốc + giọng đọc mới (khớp bằng tua giọng / giãn hình / freeze)
  gap    : cảnh không thoại (giữ âm gốc, cắt ngắn nếu quá dài)
Mỗi block render ra video (đúng N frame) + wav (đúng N mẫu) rồi nối lại → không lệch tiếng/hình.
"""
import math
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

from .utils import log, media_info, read_json, run_ffmpeg, write_json, write_srt

SR = 48000


@dataclass
class Block:
    kind: str                 # hook | speech | gap
    start: float              # giây trong video gốc
    end: float
    text: str = ""
    voice: str | None = None  # tên file trong voice/
    voice_dur: float = 0.0
    voice_tempo: float = 1.0  # >1: đọc nhanh hơn
    video_speed: float = 1.0  # <1: hình chậm lại
    freeze: float = 0.0       # giây giữ khung cuối
    out_dur: float = 0.0
    spoken: float = 0.0       # thời lượng giọng sau khi tua


# ---------------------------------------------------------------- timeline
def _subtract(a: float, b: float, cuts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    pieces = [(a, b)]
    for cs, ce in cuts:
        nxt = []
        for s, e in pieces:
            if ce <= s or cs >= e:
                nxt.append((s, e))
                continue
            if cs > s:
                nxt.append((s, cs))
            if ce < e:
                nxt.append((ce, e))
        pieces = nxt
    return pieces


def build_timeline(transcript: dict, script: dict, voices: dict, e) -> list[Block]:
    segs = {s["id"]: s for s in transcript["segments"]}
    duration = transcript["duration"]

    speech = []
    for line, v in zip(script["lines"], voices["lines"]):
        ids = line["source_ids"]
        speech.append(Block("speech", segs[ids[0]]["start"], segs[ids[-1]]["end"],
                            line["text"], v["file"], v["duration"]))
    speech.sort(key=lambda b: b.start)
    prev_end = 0.0
    for b in speech:  # chống chồng lấn
        b.start = max(b.start, prev_end)
        b.end = min(max(b.end, b.start + 0.4), duration)
        prev_end = b.end
    speech = [b for b in speech if b.end - b.start > 0.1]

    cuts = sorted((segs[i]["start"], segs[i]["end"]) for i in script.get("drop_ids", []))
    blocks: list[Block] = []
    cursor = 0.0
    for b in speech + [None]:
        gap_end = b.start if b else duration
        for s, en in _subtract(cursor, gap_end, cuts):
            if e.max_gap is not None:
                en = min(en, s + e.max_gap)
            if en - s >= 0.25:
                blocks.append(Block("gap", s, en))
        if b:
            blocks.append(b)
            cursor = b.end

    hook, hv = script.get("hook") or {}, voices.get("hook")
    if e.hook and hook.get("source_ids") and hv:
        ids = hook["source_ids"]
        blocks.insert(0, Block("hook", segs[ids[0]]["start"], segs[ids[-1]]["end"],
                               hook["text"], hv["file"], hv["duration"]))

    for b in blocks:
        _fit(b, e)
    return blocks


def _fit(b: Block, e) -> None:
    fps = e.fps
    if b.kind == "hook":  # teaser: cắt hình theo độ dài câu hook
        b.end = min(b.end, b.start + b.voice_dur + 0.4)
    d = b.end - b.start
    if b.kind == "gap":
        out = d
    else:
        v = b.voice_dur
        out = d
        if v > d:
            b.voice_tempo = min(v / d, e.max_voice_speedup)
            spoken = v / b.voice_tempo
            if spoken > d:
                b.video_speed = max(d / spoken, e.min_video_speed)
                b.freeze = max(spoken - d / b.video_speed, 0.0)
            out = spoken
        elif b.kind == "speech" and d > v + e.max_slack:
            # giọng ngắn hơn cảnh nhiều: tua hình nhanh, phần còn thừa thì cắt → không có khoảng lặng dài
            b.video_speed = min(d / (v + e.max_slack), e.max_video_speed)
            b.end = b.start + min(d, (v + e.max_slack) * b.video_speed)
            out = (b.end - b.start) / b.video_speed
        b.spoken = round(v / b.voice_tempo, 3)
    b.out_dur = math.ceil(out * fps - 1e-6) / fps  # tròn theo frame


# ---------------------------------------------------------------- render
def _even(x: float) -> int:
    x = int(x)
    return x - x % 2


def _visual(e, W: int, H: int) -> str:
    g, cur = "", "src"
    if e.blur_region:  # làm mờ dải sub gốc — trước khi lật/zoom nên toạ độ theo video gốc
        y0, y1 = e.blur_region
        g += (f"[src]split[bm][bs];[bs]crop=iw:ih*{y1 - y0}:0:ih*{y0},boxblur=12:3[bb];"
              f"[bm][bb]overlay=0:main_h*{y0}[blr];")
        cur = "blr"
    chain = []
    if e.mirror:
        chain.append("hflip")
    if e.zoom and e.zoom > 1:
        chain.append(f"crop=iw/{e.zoom}:ih/{e.zoom}")
    chain.append(f"eq=brightness={e.brightness}:contrast={e.contrast}:saturation={e.saturation}")
    g += f"[{cur}]{','.join(chain)}[base]"
    cur = "base"
    if e.aspect == "source":
        g += f";[{cur}]scale={W}:{H},setsar=1,format=yuv420p[vout]"
    else:
        sw, sh = _even(W / 8), _even(H / 8)
        g += (f";[{cur}]split[fg][bg];"
              f"[bg]scale={sw}:{sh}:force_original_aspect_ratio=increase,crop={sw}:{sh},"
              f"boxblur=6:2,scale={W}:{H}[bgb];"
              f"[fg]scale={W}:{H}:force_original_aspect_ratio=decrease[fgs];"
              f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1,format=yuv420p[vout]")
    return g


def _encoder_args(e) -> list:
    if e.encoder == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", e.crf, "-b:v", 0]
    return ["-c:v", e.encoder, "-preset", "veryfast", "-crf", e.crf]


def _render_block(i: int, b: Block, source: Path, work: Path, has_audio: bool, W: int, H: int, e) -> None:
    build = work / "build"
    d = b.end - b.start
    frames = round(b.out_dur * e.fps)
    args = ["-ss", f"{b.start:.3f}", "-t", f"{d:.3f}", "-i", source]
    voice_idx = null_idx = None
    if b.voice:
        args += ["-i", work / "voice" / b.voice]
        voice_idx = 1
    if not has_audio:
        null_idx = 2 if b.voice else 1
        args += ["-f", "lavfi", "-t", f"{d:.3f}", "-i", f"anullsrc=r={SR}:cl=stereo"]

    v = "[0:v]setpts=PTS-STARTPTS"
    if b.video_speed != 1:
        v += f",setpts=PTS/{b.video_speed:.4f}"
    v += f",fps={e.fps},tpad=stop_mode=clone:stop_duration={b.freeze + 0.5:.3f}[src];"
    v += _visual(e, W, H)

    norm = f"aresample={SR},aformat=sample_fmts=fltp:channel_layouts=stereo"
    a = f"[{null_idx if null_idx is not None else 0}:a]asetpts=PTS-STARTPTS,{norm}"
    if b.video_speed != 1:
        a += f",atempo={b.video_speed:.4f}"
    vol = e.gap_audio_volume if b.kind == "gap" else e.original_audio_volume
    tail = f"atrim=end={b.out_dur:.4f},apad=whole_dur={b.out_dur:.4f}[aout]"
    if voice_idx is not None:
        tempo = f",atempo={b.voice_tempo:.4f}" if b.voice_tempo != 1 else ""
        a += (f",volume={vol}[oa];[{voice_idx}:a]{norm}{tempo}[va];"
              f"[oa][va]amix=inputs=2:duration=longest:normalize=0,{tail}")
    else:
        a += f",volume={vol},{tail}"

    run_ffmpeg([
        *args, "-filter_complex", f"{v};{a}",
        "-map", "[vout]", "-frames:v", frames, "-r", e.fps, *_encoder_args(e),
        "-g", e.fps * 2, "-an", build / f"block_{i:03d}.mp4",
        "-map", "[aout]", "-c:a", "pcm_s16le", "-ar", SR, build / f"block_{i:03d}.wav",
    ])


# ---------------------------------------------------------------- subtitles
def _chunks(text: str, max_words: int = 7) -> list[str]:
    out = []
    for sent in re.split(r"(?<=[.,!?;:…])\s+", text.strip()):
        words = sent.split()
        n = max(1, math.ceil(len(words) / max_words))
        size = math.ceil(len(words) / n)
        out += [" ".join(words[k:k + size]) for k in range(0, len(words), size)]
    return [c for c in out if c]


def _ass_time(t: float) -> str:
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _band_margin(e, W: int, H: int, src_w: int, src_h: int, fs: int) -> int | None:
    """MarginV để sub mới nằm đúng dải sub gốc đã làm mờ (tính qua zoom + co khung)."""
    if not e.blur_region or not src_w or not src_h:
        return None
    z = e.zoom if e.zoom and e.zoom > 1 else 1.0
    zw, zh = src_w / z, src_h / z
    fg_h = H if e.aspect == "source" else zh * min(W / zw, H / zh)
    top = (H - fg_h) / 2
    y_center = (sum(e.blur_region) / 2 - (1 - 1 / z) / 2) * z
    return max(0, round(H - (top + y_center * fg_h) - fs / 2))


def build_subs(blocks: list[Block], W: int, H: int, e, src_w: int = 0, src_h: int = 0) -> tuple[str, list]:
    k = W / 1080
    fs = round(e.font_size * k)
    margin = _band_margin(e, W, H, src_w, src_h, fs)
    if margin is None:
        margin = round(H * (0.25 if H > W else 0.08))
    head = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {W}\nPlayResY: {H}\nWrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, "
        "Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{e.font},{fs},&H00FFFFFF,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,"
        f"{max(2, round(4 * k))},1,2,{round(60 * k)},{round(60 * k)},{margin},1\n"
        f"Style: Hook,{e.font},{round(fs * 1.3)},&H0000FFFF,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,"
        f"0,0,1,{max(3, round(6 * k))},2,8,{round(60 * k)},{round(60 * k)},{round(H * 0.15)},1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    events, cues, t = [], [], 0.0
    for b in blocks:
        if b.text and b.spoken > 0:
            parts = _chunks(b.text)
            total = sum(len(p) for p in parts)
            cur = t
            for p in parts:
                dur = b.spoken * len(p) / total
                clean = p.replace("{", "(").replace("}", ")").replace("\n", " ")
                style = "Hook" if b.kind == "hook" else "Default"
                events.append(f"Dialogue: 0,{_ass_time(cur)},{_ass_time(cur + dur)},{style},,0,0,0,,{clean}")
                cues.append((cur, cur + dur, p))
                cur += dur
        t += b.out_dur
    return head + "\n".join(events) + "\n", cues


# ---------------------------------------------------------------- main
def edit(work: Path, cfg) -> Path:
    e = cfg.edit
    source = work / "source.mp4"
    info = media_info(source)
    if e.aspect == "source":
        W, H = _even(info["width"]), _even(info["height"])
    elif e.aspect == "16:9":
        W, H = max(e.width, e.height), min(e.width, e.height)
    else:
        W, H = min(e.width, e.height), max(e.width, e.height)

    transcript = read_json(work / "transcript.json")
    script = read_json(work / "script.json")
    voices = read_json(work / "voices.json")
    blocks = build_timeline(transcript, script, voices, e)

    build = work / "build"
    build.mkdir(exist_ok=True)
    for old in build.glob("block_*"):
        old.unlink()
    total = sum(b.out_dur for b in blocks)
    log.info("Dựng %d block → %.1fs (gốc %.1fs), khung %dx%d", len(blocks), total, info["duration"], W, H)

    workers = 3 if e.encoder == "h264_nvenc" else 4
    with ThreadPoolExecutor(workers) as pool:
        futures = [pool.submit(_render_block, i, b, source, work, info["has_audio"], W, H, e)
                   for i, b in enumerate(blocks)]
        for n, f in enumerate(futures, 1):
            f.result()
            if n % 10 == 0 or n == len(futures):
                log.info("  render %d/%d block", n, len(futures))

    for kind in ("mp4", "wav"):
        (build / f"list_{kind}.txt").write_text(
            "".join(f"file 'block_{i:03d}.{kind}'\n" for i in range(len(blocks))), encoding="utf-8")
    run_ffmpeg(["-f", "concat", "-safe", 0, "-i", "list_mp4.txt", "-c", "copy", "video.mp4"], cwd=build)
    run_ffmpeg(["-f", "concat", "-safe", 0, "-i", "list_wav.txt", "-c", "copy", "audio.wav"], cwd=build)

    ass, cues = build_subs(blocks, W, H, e, info["width"], info["height"])
    (work / "subs.ass").write_text(ass, encoding="utf-8")
    write_srt(work / "subs.srt", cues)  # để import vào CapCut nếu muốn chỉnh tay
    write_json(work / "timeline.json", [asdict(b) for b in blocks])

    out = _final(work, e, with_subs=e.subtitles)
    log.info("Xong video: %s", out)
    return out


def _final(work: Path, e, with_subs: bool) -> Path:
    args = ["-i", "build/video.mp4", "-i", "build/audio.wav"]
    af = "[1:a]anull[a]"
    if e.bgm:
        args += ["-stream_loop", -1, "-i", Path(e.bgm).resolve()]
        af = f"[2:a]volume={e.bgm_volume}[bg];[1:a][bg]amix=inputs=2:duration=first:normalize=0[a]"
    # chạy với cwd=work để đường dẫn subs.ass không cần escape kiểu Windows (C\:)
    vf = "[0:v]subtitles=subs.ass[v]" if with_subs else "[0:v]null[v]"
    try:
        run_ffmpeg([*args, "-filter_complex", f"{vf};{af}", "-map", "[v]", "-map", "[a]",
                    *_encoder_args(e), "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                    "-shortest", "output.mp4"], cwd=work)
    except RuntimeError as err:
        if not with_subs:
            raise
        log.warning("Không burn được phụ đề (ffmpeg thiếu libass?) — xuất không sub, dùng subs.srt. %s",
                    str(err)[-300:])
        return _final(work, e, with_subs=False)
    return work / "output.mp4"
