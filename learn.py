from __future__ import annotations

import json
import random
import sqlite3
from typing import Any, Dict, List, Optional, Tuple


TEXT_KEYS = (
    "skin_tone_prompt_photo_plus_inpaint",
    "skin_tone_prompt_video",
    "positive_inpaint_1",
    "negative_inpaint_1",
    "positive_inpaint_2_lower_body",
    "negative_inpaint_2",
    "video_motion_prompt",
    "video_negative",
)

NUMERIC_KEYS = ("steps", "cfg", "seed", "frames", "fps")


def _parse_run_json(row: sqlite3.Row) -> Dict[str, Any]:
    if not row["run_json"]:
        return {}
    try:
        return json.loads(row["run_json"])
    except Exception:
        return {}


def _weighted_pick(runs: List[Tuple[sqlite3.Row, Dict[str, Any]]], key: str) -> Any:
    """Pick value from top runs; numeric = weighted avg, text = best run's value."""
    values: List[Tuple[float, Any]] = []
    for row, run in runs:
        weight = float(row["weighted_score"])
        if key in run and run[key] is not None:
            values.append((weight, run[key]))
            continue
        nodes = run.get("nodes") or {}
        for label, node in nodes.items():
            if not isinstance(node, dict):
                continue
            if key.replace("_", " ") in label.lower().replace("_", " "):
                if node.get("text"):
                    values.append((weight, node["text"]))
                break

    if not values:
        return None

    if isinstance(values[0][1], str):
        return max(values, key=lambda x: x[0])[1]

    total_w = sum(w for w, _ in values)
    if total_w <= 0:
        return values[0][1]
    return sum(w * float(v) for w, v in values) / total_w


def build_profile(conn: sqlite3.Connection, workflow: str) -> Optional[Dict[str, Any]]:
    from .db import rated_runs

    rows = rated_runs(conn, workflow)
    if not rows:
        return None

    top_n = rows[: min(10, len(rows))]
    parsed = [(row, _parse_run_json(row)) for row in top_n]

    settings: Dict[str, Any] = {}
    for key in NUMERIC_KEYS:
        val = _weighted_pick(parsed, key)
        if val is not None:
            if key in ("steps", "seed", "frames", "fps"):
                settings[key] = int(round(float(val)))
            elif key == "cfg":
                settings[key] = round(float(val), 2)
            else:
                settings[key] = val

    for key in TEXT_KEYS:
        val = _weighted_pick(parsed, key)
        if isinstance(val, str) and val.strip():
            settings[key] = val.strip()

    best_row = top_n[0]
    return {
        "workflow": workflow,
        "rating_count": len(rows),
        "best_generation_id": best_row["generation_id"],
        "best_weighted_score": round(float(best_row["weighted_score"]), 2),
        "best_overall": best_row["overall"],
        "settings": settings,
    }


def mutate_profile(profile: Dict[str, Any], *, explore: float = 0.15) -> Dict[str, Any]:
    """Small random exploration around learned settings."""
    out = json.loads(json.dumps(profile))
    settings = out.setdefault("settings", {})

    def bump_int(key: str, lo: int, hi: int, step: int) -> None:
        if key not in settings:
            return
        if random.random() > explore:
            return
        settings[key] = max(lo, min(hi, int(settings[key]) + random.choice([-step, step])))

    def bump_float(key: str, lo: float, hi: float, step: float) -> None:
        if key not in settings:
            return
        if random.random() > explore:
            return
        settings[key] = round(max(lo, min(hi, float(settings[key]) + random.choice([-step, step]))), 2)

    bump_int("steps", 10, 80, 4)
    bump_float("cfg", 1.0, 14.0, 0.5)
    bump_int("seed", 1, 2**31 - 1, 1337)
    bump_int("frames", 16, 200, 8)
    bump_int("fps", 8, 30, 2)
    return out
