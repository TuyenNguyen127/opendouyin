import argparse
import logging
import os
import sys
import time
import warnings

from dotenv import load_dotenv

from .config import ROOT, load_config
from .discover import discover
from .pipeline import STEPS, run
from .utils import log, write_json


def _print_table(videos: list[dict]) -> None:
    print(f"{'#':>2}  {'điểm':>6}  {'view':>9}  {'tương tác':>9}  {'thời lượng':>10}  tiêu đề / link")
    for i, v in enumerate(videos, 1):
        dur = f"{v['duration'] // 60}:{v['duration'] % 60:02d}"
        print(f"{i:>2}  {v['score']:>6.2f}  {v['views']:>9,}  {v['engagement']:>9.2%}  {dur:>10}  "
              f"{v['title'][:50]}\n{'':>44}{v['url']}")


def cmd_discover(args, cfg):
    videos = discover(cfg)
    out = cfg.workdir / f"discover_{time.strftime('%Y%m%d_%H%M%S')}.json"
    cfg.workdir.mkdir(parents=True, exist_ok=True)
    write_json(out, videos)
    _print_table(videos)
    print(f"\nĐã lưu: {out}")


def cmd_run(args, cfg):
    work = run(cfg, source=args.source, name=args.name, start=args.start, until=args.until)
    print(f"\nXong. Kết quả trong: {work}")


def cmd_resume(args, cfg):
    work = run(cfg, name=args.project, start=args.start, until=args.until)
    print(f"\nXong. Kết quả trong: {work}")


def cmd_batch(args, cfg):
    videos = discover(cfg)[: args.top]
    _print_table(videos)
    for v in videos:
        try:
            run(cfg, source=v["url"], until=args.until)
        except Exception as e:  # một video lỗi không chặn cả lô
            log.error("Bỏ qua %s: %s", v["url"], e)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):  # console Windows mặc định cp1252
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    load_dotenv(ROOT / ".env")
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    os.environ.setdefault("HF_HUB_VERBOSITY", "error")
    p = argparse.ArgumentParser(prog="opendouyin", description="Clone video hot Trung Quốc thành video tiếng Việt")
    p.add_argument("-c", "--config", help="file config YAML ghi đè config.yaml")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    def discover_opts(sp):
        sp.add_argument("--source", choices=["popular", "ranking", "search"])
        sp.add_argument("--keyword", help="từ khoá tìm kiếm (tiếng Trung cho kết quả tốt nhất)")
        sp.add_argument("--min-views", type=int)
        sp.add_argument("--max-duration", type=int)

    d = sub.add_parser("discover", help="#1 tìm video hot trên Bilibili")
    discover_opts(d)
    d.add_argument("--limit", type=int)
    d.set_defaults(func=cmd_discover)

    r = sub.add_parser("run", help="#2→#6 clone 1 video từ link hoặc file")
    r.add_argument("source", help="link Bilibili/Douyin/YouTube/TikTok hoặc file .mp4")
    r.add_argument("--name", help="tên thư mục project")
    r.add_argument("--from", dest="start", choices=STEPS, help="chạy lại từ bước này")
    r.add_argument("--until", choices=STEPS, help="dừng sau bước này (vd: rewrite để sửa script)")
    r.set_defaults(func=cmd_run)

    rs = sub.add_parser("resume", help="chạy tiếp project đã có (sau khi sửa script.json...)")
    rs.add_argument("project")
    rs.add_argument("--from", dest="start", choices=STEPS)
    rs.add_argument("--until", choices=STEPS)
    rs.set_defaults(func=cmd_resume)

    b = sub.add_parser("batch", help="tìm video hot rồi clone luôn top N")
    discover_opts(b)
    b.add_argument("--top", type=int, default=3)
    b.add_argument("--until", choices=STEPS)
    b.set_defaults(func=cmd_batch)

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    for noisy in ("httpx", "httpx2", "httpcore", "httpcore2", "huggingface_hub", "faster_whisper"):
        logging.getLogger(noisy).setLevel(logging.ERROR)
    warnings.filterwarnings("ignore", module="huggingface_hub")

    cfg = load_config(args.config)
    for key in ("source", "keyword", "min_views", "max_duration", "limit"):
        val = getattr(args, key, None)
        if val is not None and args.cmd in ("discover", "batch"):
            cfg.discover[key] = val
    if getattr(args, "keyword", None) and not getattr(args, "source", None):
        cfg.discover["source"] = "search"

    try:
        args.func(args, cfg)
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        if args.verbose:
            raise
        log.error("%s", e)
        sys.exit(1)
