"""Swiss demographic data lookup.

The honest path here for a demo: BFS PxWeb requires authenticated multi-step
queries and the STATPOP CSV is 100+ MB. For a 15-minute analyst flow, we
seed `data/bfs/municipalities.csv` with the top ~25 Swiss municipalities by
relevance to retail (BFS STATPOP 2024 + canton purchasing-power index).

When the geocoded city falls outside the seed set, the function returns a
"data unavailable" stub so the UI degrades gracefully. To extend coverage,
append rows to the CSV — no code change required.

For the 5★ hotel premium signal we hit OSM directly via `utils.osm`.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd

from utils.config import ROOT

BFS_CSV = ROOT / "data" / "bfs" / "municipalities.csv"


@lru_cache(maxsize=1)
def _seed_df() -> pd.DataFrame:
    if not BFS_CSV.exists():
        return pd.DataFrame()
    return pd.read_csv(BFS_CSV)


def _normalize_city_name(name: str | None) -> str:
    if not name:
        return ""
    return name.strip().lower().replace("ü", "u").replace("é", "e").replace("è", "e")


def lookup_municipality(city: str | None) -> dict[str, Any] | None:
    """Return the seeded BFS row for `city` (case- and umlaut-insensitive)."""
    if not city:
        return None
    df = _seed_df()
    if df.empty:
        return None
    needle = _normalize_city_name(city)
    for _, row in df.iterrows():
        if _normalize_city_name(str(row["city"])) == needle:
            return row.to_dict()
        # Loose contains-match for "Zürich" vs. "Zurich" etc.
        if needle in _normalize_city_name(str(row["city"])):
            return row.to_dict()
    return None


def coverage_summary() -> dict[str, int]:
    df = _seed_df()
    return {
        "municipalities": len(df),
        "total_population": int(df["population"].sum()) if not df.empty else 0,
    }
