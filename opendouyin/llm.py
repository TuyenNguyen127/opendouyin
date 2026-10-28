"""Gọi LLM với output có cấu trúc (Pydantic) — dùng chung cho rewrite / SEO / vision.

provider:
  manual : ghi prompt ra file, người dùng dán vào ChatGPT/Gemini/claude.ai rồi lưu câu trả lời
  ollama : model chạy local (miễn phí, offline)
  claude : Claude API (cần ANTHROPIC_API_KEY)
"""
import base64
import json
import os
import re
from pathlib import Path
from typing import TypeVar

import requests
from pydantic import BaseModel, ValidationError

from .utils import log

T = TypeVar("T", bound=BaseModel)


class ManualStep(Exception):
    """Dừng pipeline để người dùng tự hỏi chatbot; chạy `resume` sau khi có câu trả lời."""


def ask(cfg, task: str, work: Path, system: str, content: str | list, schema: type[T]) -> T:
    provider = cfg.llm.provider
    if provider == "manual":
        return _manual(task, work, system, content, schema)
    if provider == "ollama":
        return _ollama(cfg.llm, system, content, schema)
    if provider == "claude":
        return _claude(cfg.llm.claude_model, system, content, schema)
    raise ValueError(f"llm.provider không hợp lệ: {provider}")


def supports_images(cfg) -> bool:
    return cfg.llm.provider == "claude"


# ---------------------------------------------------------------- manual
def _parse_json(text: str, schema: type[T]) -> T:
    text = text.strip()
    if m := re.search(r"```(?:json)?\s*(.*?)```", text, re.S):  # chatbot hay bọc trong ```json
        text = m.group(1)
    start, end = text.find("{"), text.rfind("}")
    return schema.model_validate_json(text[start:end + 1] if start >= 0 else text)


def _manual(task: str, work: Path, system: str, content, schema: type[T]) -> T:
    if not isinstance(content, str):
        raise RuntimeError("Chế độ manual không gửi được hình ảnh — dùng llm.provider: claude")
    prompt_file, answer_file = work / f"{task}.prompt.txt", work / f"{task}.answer.json"
    if answer_file.exists() and answer_file.read_text(encoding="utf-8").strip():
        try:
            return _parse_json(answer_file.read_text(encoding="utf-8"), schema)
        except (ValidationError, ValueError) as e:
            raise RuntimeError(f"{answer_file.name} không đúng định dạng JSON yêu cầu:\n{e}") from e

    schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False, indent=1)
    prompt_file.write_text(
        f"{system}\n\n-----\n{content}\n\n-----\n"
        "CHỈ trả lời bằng MỘT khối JSON hợp lệ (không giải thích thêm), đúng theo JSON Schema sau:\n"
        f"{schema_json}\n",
        encoding="utf-8",
    )
    answer_file.touch()
    if os.name == "nt":
        try:
            os.startfile(prompt_file)
        except OSError:
            pass
    raise ManualStep(
        f"Cần bạn hỏi chatbot cho bước '{task}':\n"
        f"  1. Mở {prompt_file}, copy toàn bộ, dán vào ChatGPT / Gemini / claude.ai\n"
        f"  2. Copy câu trả lời JSON, dán vào {answer_file} rồi lưu\n"
        f"  3. Chạy lại: python -m opendouyin resume {work.name}"
    )


# ---------------------------------------------------------------- ollama
def _ollama(c, system: str, content, schema: type[T]) -> T:
    if not isinstance(content, str):
        raise RuntimeError("Chế độ ollama chưa hỗ trợ đọc hình ảnh — dùng llm.provider: claude")
    log.info("Ollama %s đang xử lý...", c.ollama_model)
    try:
        r = requests.post(
            f"{c.ollama_url}/api/chat",
            json={
                "model": c.ollama_model,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
                "format": schema.model_json_schema(),
                "stream": False,
                "think": False,
                "options": {"num_ctx": 16384, "temperature": 0.7},
            },
            timeout=1800,
        )
    except requests.ConnectionError as e:
        raise RuntimeError(f"Không kết nối được Ollama ở {c.ollama_url} — đã cài và mở Ollama chưa?") from e
    if r.status_code == 404:
        raise RuntimeError(f"Chưa có model, chạy: ollama pull {c.ollama_model}")
    r.raise_for_status()
    try:
        return _parse_json(r.json()["message"]["content"], schema)
    except (ValidationError, ValueError) as e:
        raise RuntimeError(f"Ollama trả về JSON sai định dạng — thử model lớn hơn.\n{e}") from e


# ---------------------------------------------------------------- claude
def _claude(model: str, system: str, content, schema: type[T]) -> T:
    import anthropic

    try:
        resp = anthropic.Anthropic().beta.messages.parse(
            model=model,
            max_tokens=16000,
            system=system,
            messages=[{"role": "user", "content": content}],
            output_format=schema,
            # tự chuyển sang model dự phòng nếu bị bộ lọc an toàn từ chối
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.AuthenticationError as e:
        raise RuntimeError("Thiếu/sai ANTHROPIC_API_KEY (đặt trong file .env)") from e
    except anthropic.RateLimitError as e:
        raise RuntimeError("Claude API đang giới hạn tốc độ, thử lại sau ít phút") from e
    except anthropic.APIStatusError as e:
        raise RuntimeError(f"Claude API lỗi {e.status_code}: {e.message}") from e

    if resp.stop_reason == "refusal":
        reason = resp.stop_details.explanation if resp.stop_details else ""
        raise RuntimeError(f"Claude từ chối xử lý nội dung này. {reason}")
    if resp.stop_reason == "max_tokens":
        raise RuntimeError("Kết quả quá dài, vượt max_tokens — thử video ngắn hơn")
    u = resp.usage
    log.info("Claude (%s): %d in / %d out tokens", resp.model, u.input_tokens, u.output_tokens)
    return resp.parsed_output


def image_block(path: Path) -> dict:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/jpeg",
            "data": base64.b64encode(Path(path).read_bytes()).decode(),
        },
    }
