"""Load config.yaml and .env once. All modules read tunable numbers from CFG."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


def _load() -> dict[str, Any]:
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


CFG: dict[str, Any] = _load()


def path(key: str) -> Path:
    """Return an absolute path for a key in the `paths` section."""
    return ROOT / CFG["paths"][key]


def salt() -> str:
    """Return the salt for pseudonyms and user-ID hashing."""
    return os.environ.get(CFG["privacy"]["salt_env"], CFG["privacy"]["default_salt"])


def require_keys() -> None:
    """Stop with a clear message if a required key is missing in .env."""
    missing = [k for k in ("HF_TOKEN", "OPENROUTER_API_KEY") if not os.environ.get(k)]
    if missing:
        raise SystemExit(f"STOP: .env does not have {', '.join(missing)}. Add the keys to .env in the repository root.")
