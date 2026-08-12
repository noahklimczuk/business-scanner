"""Config loading.

`config.json` holds the API key and is gitignored. `config.example.json` ships.
The key can also come from the environment (`GOOGLE_API_KEY`, optionally via a
`.env` file) so the Linux VM can run this without a key sitting on disk.
"""
from __future__ import annotations

import json
import os
from typing import Any

try:  # optional: only needed if the operator keeps the key in .env
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover - dotenv is a listed dep, but don't die
    pass

ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.environ.get("LEADSMITH_CONFIG") or os.path.join(ROOT, "config.json")
EXAMPLE_PATH = os.path.join(ROOT, "config.example.json")

DEFAULTS: dict[str, Any] = {
    "radius_km": 10,
    "cell_m": 900,
    "region_code": "CA",
    "confirm_above_usd": 25.0,
}


class ConfigError(Exception):
    """Raised with a message written for a human at 9pm, not a stack trace."""


def load() -> dict[str, Any]:
    cfg: dict[str, Any] = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as fh:
                cfg = json.load(fh)
        except json.JSONDecodeError as exc:
            raise ConfigError(
                f"{CONFIG_PATH} is not valid JSON ({exc.msg}, line {exc.lineno}).\n"
                f"Open it and fix the syntax, or copy config.example.json over it."
            ) from exc

    cfg.setdefault("business", {})
    cfg["defaults"] = {**DEFAULTS, **cfg.get("defaults", {})}
    return cfg


def api_key(cfg: dict[str, Any] | None = None) -> str:
    """The Places API key, from config.json or GOOGLE_API_KEY."""
    cfg = load() if cfg is None else cfg
    key = (cfg.get("google_api_key") or "").strip()
    if key.startswith("PASTE_"):
        key = ""
    key = key or os.environ.get("GOOGLE_API_KEY", "").strip()
    if not key:
        raise ConfigError(
            "No Google Places API key.\n"
            f"  1. cp {os.path.basename(EXAMPLE_PATH)} {os.path.basename(CONFIG_PATH)}\n"
            "  2. paste your key into the google_api_key field\n"
            "  (or export GOOGLE_API_KEY instead — config.json is gitignored either way)\n"
            "The key needs Places API (New) enabled, restricted to that API and to your IP."
        )
    return key
