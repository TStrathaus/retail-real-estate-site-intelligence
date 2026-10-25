"""Config loaders for brand parameters and score weights.

The two JSON files in `data/config/` make the tool brand-neutral:
swap `on_brand.json` and the same scoring engine runs for any retailer.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "data" / "config"
DB_PATH = ROOT / "data" / "db" / "retail_si.db"
CACHE_DIR = ROOT / "data" / "cache"
OUTPUT_DIR = ROOT / "outputs"


@lru_cache(maxsize=1)
def load_brand() -> dict[str, Any]:
    return json.loads((CONFIG_DIR / "on_brand.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def load_weights() -> dict[str, float]:
    return json.loads((CONFIG_DIR / "score_weights.json").read_text(encoding="utf-8"))


def presets_only_mode() -> bool:
    """True when Step 1 is limited to the demo presets (no free-text address).

    On by default on Streamlit Community Cloud, where only the presets' OSM
    data is seeded; `PRESETS_ONLY=0/1` overrides it either way.
    """
    flag = os.environ.get("PRESETS_ONLY")
    if flag is not None:
        return flag.strip().lower() in ("1", "true", "yes")
    return Path("/mount/src").exists()  # Streamlit Community Cloud checkout


def save_weights(weights: dict[str, float]) -> None:
    path = CONFIG_DIR / "score_weights.json"
    path.write_text(json.dumps(weights, indent=2), encoding="utf-8")
    load_weights.cache_clear()
