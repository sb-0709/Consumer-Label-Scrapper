"""Load config.yaml (+ .env overrides)."""
from __future__ import annotations

import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SQL_DIR = ROOT / "sql"

try:  # optional
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:  # pragma: no cover
    pass


def load_config(path: str | os.PathLike | None = None) -> dict:
    path = Path(path or os.getenv("CLS_CONFIG") or ROOT / "config.yaml")
    with open(path) as f:
        cfg = yaml.safe_load(f)
    if os.getenv("GCP_PROJECT"):
        cfg["bigquery"]["project"] = os.environ["GCP_PROJECT"]
    out = Path(cfg["scraper"].get("output_dir", "outputs"))
    cfg["scraper"]["output_dir"] = str(out if out.is_absolute() else ROOT / out)
    Path(cfg["scraper"]["output_dir"]).mkdir(parents=True, exist_ok=True)
    return cfg


def output_dir(cfg: dict) -> Path:
    return Path(cfg["scraper"]["output_dir"])
