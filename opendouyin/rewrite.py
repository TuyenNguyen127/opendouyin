"""Bước 3b — Dịch + viết lại thành script mới tự nhiên hơn (thay cho ChatGPT/Gemini thủ công)."""
import re

from pydantic import BaseModel, Field

from . import llm
from .utils import log, write_json


class Line(BaseModel):
    source_ids: list[int] = Field(description="id các đoạn gốc liên tiếp mà câu này thay thế")
    text: str = Field(description="lời kể mới bằng ngôn ngữ đích")


class Hook(BaseModel):
    source_ids: list[int] = Field(description="id đoạn gốc có cảnh cao trào nhất để làm teaser")
    text: str = Field(description="1 câu mở đầu gây tò mò, đọc dưới 4 giây")


class Script(BaseModel):
    summary: str = Field(description="tóm tắt nội dung 2-3 câu")
    hook: Hook
    lines: list[Line]
    drop_ids: list[int] = Field(description="id các đoạn nhàm chán/thừa nên cắt khỏi video")


SYSTEM = """Bạn là biên kịch content short-video chuyên "xào lại" video Trung Quốc cho kênh {lang}.
Nhiệm vụ: từ transcript gốc (có id và mốc thời gian), viết lại thành script lồng tiếng MỚI:
- Không dịch word-by-word. Viết lại tự nhiên, giọng {style}.
- Giữ đúng mạch truyện và plot twist; được gộp nhiều đoạn gốc liên tiếp vào một câu.
- Mỗi Line ứng với 1–2 đoạn gốc LIÊN TIẾP (tối đa 3), tăng dần, mỗi id chỉ dùng một lần — hình ảnh
  của các đoạn đó sẽ được ghép với câu thoại này, nên nội dung phải khớp với cảnh.
- ĐỘ DÀI: giọng đọc khoảng {cps} ký tự/giây. Câu thoại không nên dài hơn
  (tổng thời lượng các đoạn gốc × {cps}) ký tự, nếu không giọng sẽ bị tua nhanh.
- Đoạn nhàm chán, lặp, chào hỏi, kêu gọi follow kênh gốc → đưa vào drop_ids.
- Hook: chọn khoảnh khắc gây sốc/tò mò nhất, viết 1 câu teaser kích thích xem tiếp
  (không spoil kết). Hook sẽ được đặt lên đầu video.
- Không nhắc tên kênh/tác giả gốc, không chèn emoji, không dùng ký tự đặc biệt khó đọc.
- TUYỆT ĐỐI không để lại chữ Hán: tên riêng/app/game phải dịch nghĩa hoặc phiên âm.
- Nội dung chỉ đúng ở Trung Quốc (số điện thoại, app, cơ quan...) thì đổi cho hợp người Việt
  hoặc nói chung chung."""


def rewrite(transcript: dict, meta: dict, work, cfg) -> dict:
    c = cfg.rewrite
    segs = transcript["segments"]
    if not segs:
        raise RuntimeError("Transcript rỗng — không có gì để viết lại")
    rows = "\n".join(
        f"[{s['id']}] {s['start']:.1f}-{s['end']:.1f}s ({s['end'] - s['start']:.1f}s): {s['text']}"
        for s in segs
    )
    note = " (transcript dựng từ khung hình, có thể là mô tả cảnh)" if transcript.get("source") == "vision" else ""
    prompt = (
        f"Tiêu đề gốc: {meta.get('title', '')}\n"
        f"Mô tả gốc: {meta.get('description', '')[:500]}\n"
        f"Thời lượng: {transcript['duration']:.0f}s\n\n"
        f"Transcript{note}:\n{rows}"
    )
    system = SYSTEM.format(lang=c.target_language, style=c.style, cps=c.chars_per_second)
    script = llm.ask(cfg, "rewrite", work, system, prompt, Script).model_dump()
    script = _sanitize(script, {s["id"]: s for s in segs})
    write_json(work / "script.json", script)
    log.info("Script mới: %d câu, hook = %r", len(script["lines"]), script["hook"]["text"])
    return script


def _sanitize(script: dict, segs: dict[int, dict]) -> dict:
    """Bỏ id không tồn tại / dùng trùng, sắp theo thời gian, tách câu gộp quá nhiều đoạn."""
    valid = set(segs)
    used: set[int] = set()
    lines = []
    for line in sorted(script["lines"], key=lambda l: min(l["source_ids"] or [10**9])):
        ids = sorted(i for i in set(line["source_ids"]) if i in valid and i not in used)
        text = _strip_cjk(line["text"])
        if ids and text:
            used.update(ids)
            for part_ids, part in _split_line(ids, text, segs):
                lines.append({"source_ids": part_ids, "text": part})
    script["lines"] = lines
    script["drop_ids"] = sorted(i for i in set(script["drop_ids"]) if i in valid and i not in used)
    script["hook"]["source_ids"] = sorted(i for i in set(script["hook"]["source_ids"]) if i in valid)
    script["hook"]["text"] = _strip_cjk(script["hook"]["text"])
    return script


def _group_sentences(sents: list[str], n: int) -> list[str]:
    """Gộp các câu thành n nhóm, độ dài (ký tự) gần đều nhau."""
    total = sum(len(s) for s in sents)
    groups, cur, acc = [], [], 0
    for idx, s in enumerate(sents):
        cur.append(s)
        acc += len(s)
        groups_left = n - len(groups) - 1
        if groups_left > 0 and (acc >= total * (len(groups) + 1) / n or len(sents) - idx - 1 == groups_left):
            groups.append(" ".join(cur))
            cur = []
    if cur:
        groups.append(" ".join(cur))
    return groups


def _split_line(ids: list[int], text: str, segs: dict[int, dict]) -> list[tuple[list[int], str]]:
    """Model nhỏ hay gộp cả chục đoạn gốc vào 1 câu dài → hình và lời lệch nhau.
    Tách theo câu, chia đoạn gốc cho từng câu theo tỉ lệ độ dài."""
    sents = [s for s in re.split(r"(?<=[.!?…])\s+", text) if s.strip()]
    if len(ids) < 3 or len(sents) < 2:
        return [(ids, text)]
    groups = _group_sentences(sents, min(len(sents), len(ids)))
    durs = [segs[i]["end"] - segs[i]["start"] for i in ids]
    total_c, total_d = sum(len(g) for g in groups), sum(durs) or 1.0
    out, j, acc_c = [], 0, 0
    for k, g in enumerate(groups):
        acc_c += len(g)
        groups_left = len(groups) - k - 1
        take = []
        while j < len(ids) - groups_left and (not take or sum(durs[:j]) / total_d < acc_c / total_c):
            take.append(ids[j])
            j += 1
        out.append((take, g))
    out[-1][0].extend(ids[j:])
    return out


_CJK = re.compile(r"[　-〿㐀-䶿一-鿿＀-￯]+")


def _strip_cjk(text: str) -> str:
    """Model nhỏ hay sót chữ Hán — giọng tiếng Việt không đọc được, nên bỏ đi và cảnh báo."""
    if _CJK.search(text):
        log.warning("Bỏ chữ Hán còn sót trong script: %s", "".join(_CJK.findall(text)))
        text = re.sub(r"\s{2,}", " ", _CJK.sub(" ", text))
    return text.strip()
