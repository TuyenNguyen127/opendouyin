from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config.yaml"


class Section(dict):
    """dict cho phép truy cập kiểu thuộc tính: cfg.edit.zoom"""

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError as e:
            raise AttributeError(key) from e


def _wrap(value):
    if isinstance(value, dict):
        return Section({k: _wrap(v) for k, v in value.items()})
    return value


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_config(path: str | Path | None = None) -> Section:
    data = yaml.safe_load(DEFAULT_CONFIG.read_text(encoding="utf-8")) or {}
    if path and Path(path).resolve() != DEFAULT_CONFIG:
        data = _merge(data, yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {})
    cfg = _wrap(data)
    workdir = Path(cfg.workdir)
    cfg["workdir"] = workdir if workdir.is_absolute() else ROOT / workdir
    return cfg
