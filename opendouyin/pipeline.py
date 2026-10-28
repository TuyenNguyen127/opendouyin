"""Chạy toàn bộ quy trình; mỗi bước lưu kết quả vào projects/<id>/ nên có thể dừng, sửa tay, chạy tiếp."""
from pathlib import Path

from .download import download, project_id
from .edit import edit
from .llm import ManualStep
from .rewrite import rewrite
from .seo import seo
from .thumbnail import thumbnail
from .transcribe import transcribe
from .tts import synthesize
from .utils import log, read_json, write_json

STEPS = ["download", "transcribe", "rewrite", "tts", "edit", "seo", "thumbnail"]
OUTPUTS = {
    "download": "source.mp4",
    "transcribe": "transcript.json",
    "rewrite": "script.json",
    "tts": "voices.json",
    "edit": "output.mp4",
    "seo": "seo.json",
    "thumbnail": "thumbnail.jpg",
}


def _run_step(step: str, work: Path, cfg, source: str | None) -> None:
    meta_path = work / "meta.json"
    meta = read_json(meta_path) if meta_path.exists() else {}
    if step == "download":
        if not source:
            raise ValueError("Cần link/đường dẫn video để tải")
        write_json(meta_path, download(source, work, cfg))
    elif step == "transcribe":
        transcribe(work / "source.mp4", work, cfg)
    elif step == "rewrite":
        rewrite(read_json(work / "transcript.json"), meta, work, cfg)
    elif step == "tts":
        write_json(work / "voices.json", synthesize(read_json(work / "script.json"), work, cfg))
    elif step == "edit":
        edit(work, cfg)
    elif step == "seo":
        seo(read_json(work / "script.json"), meta, work, cfg)
    elif step == "thumbnail":
        text = read_json(work / "seo.json")["thumbnail_text"]
        thumbnail(work, text, cfg)


def run(cfg, source: str | None = None, name: str | None = None,
        start: str | None = None, until: str | None = None) -> Path:
    """start: chạy lại từ bước này (bỏ qua cache); until: dừng sau bước này."""
    pid = name or project_id(source)
    work = Path(cfg.workdir) / pid
    work.mkdir(parents=True, exist_ok=True)
    log.info("Project: %s", work)

    first = STEPS.index(start) if start else 0
    last = STEPS.index(until) if until else len(STEPS) - 1
    rerun = False  # một bước đã chạy lại thì mọi bước sau cũng phải chạy lại
    for i, step in enumerate(STEPS[: last + 1]):
        rerun = rerun or (start is not None and i >= first)
        if not rerun and (work / OUTPUTS[step]).exists():
            log.info("[%d/%d] %s — đã có, bỏ qua", i + 1, len(STEPS), step)
            continue
        rerun = True
        log.info("[%d/%d] %s", i + 1, len(STEPS), step)
        try:
            _run_step(step, work, cfg, source)
        except ManualStep as wait:
            log.info("Tạm dừng.\n%s", wait)
            return work
    return work
