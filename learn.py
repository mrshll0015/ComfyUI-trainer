from __future__ import annotations

import json
import random
import sqlite3
from typing import Any, Dict, List, Optional, Tuple


TEXT_KEYS = ("action",)

NUMERIC_KEYS = ("steps", "cfg", "seed", "frames", "fps")


def _node_label(node: Dict[str, Any]) -> str:
    return (node.get("properties") or {}).get("Node name for S&R", "")


def capture_run_json_from_workflow(
    workflow: Dict[str, Any],
    *,
    prompt_profile: str = "prompt_1",
    source_image: Optional[str] = None,
    action: Optional[str] = None,
) -> Dict[str, Any]:
    """Snapshot action + sampler settings used for a queued run."""
    from .prompts_store import get_action, get_profile, get_training_settings

    action_text = (action if action is not None else get_action(prompt_profile)).strip()
    training = get_training_settings()
    run: Dict[str, Any] = {
        "nodes": {},
        "prompt_profile": prompt_profile,
        "action": action_text,
        "max_dimension": training["max_dimension"],
        "frames": training["video_frames"],
    }
    if source_image:
        run["source_image"] = source_image

    profile_nodes = get_profile(prompt_profile, action_override=action_text)
    for label, text in profile_nodes.items():
        if label.startswith("shared:"):
            continue
        if isinstance(text, str) and text.strip():
            run["nodes"][label] = {"type": "CLIPTextEncode", "text": text.strip()}

    for node in workflow.get("nodes", []):
        class_type = node.get("type", "")
        wv = node.get("widgets_values") or []
        label = _node_label(node) or f"{class_type}_{node.get('id')}"

        if class_type == "KSampler" and isinstance(wv, list) and len(wv) >= 4:
            run["nodes"][label] = {
                "type": class_type,
                "seed": wv[0],
                "steps": wv[2],
                "cfg": wv[3],
            }
            run.setdefault("seed", wv[0])
            run.setdefault("steps", wv[2])
            run.setdefault("cfg", wv[3])

        elif class_type == "WanImageToVideo" and isinstance(wv, list) and wv:
            run["nodes"][label] = {"type": class_type, "length": wv[0]}
            run["frames"] = wv[0]

        elif class_type == "VHS_VideoCombine" and isinstance(wv, dict):
            fps = wv.get("frame_rate")
            run["nodes"][label] = {"type": class_type, "frame_rate": fps}
            if isinstance(fps, (int, float)):
                run.setdefault("fps", int(fps))

    return run


def learn_status(conn: sqlite3.Connection, workflow: str) -> Dict[str, Any]:
    from .db import rated_runs

    total = int(
        conn.execute(
            "SELECT COUNT(*) FROM generations WHERE workflow = ?",
            (workflow,),
        ).fetchone()[0]
    )
    with_run = int(
        conn.execute(
            """
            SELECT COUNT(*) FROM generations
            WHERE workflow = ? AND run_json IS NOT NULL AND trim(run_json) NOT IN ('', '{}')
            """,
            (workflow,),
        ).fetchone()[0]
    )
    rated = int(
        conn.execute(
            """
            SELECT COUNT(*) FROM ratings r
            JOIN generations g ON g.id = r.generation_id
            WHERE g.workflow = ?
            """,
            (workflow,),
        ).fetchone()[0]
    )
    usable_rated = int(
        conn.execute(
            """
            SELECT COUNT(*) FROM generations g
            JOIN ratings r ON r.generation_id = g.id
            WHERE g.workflow = ? AND g.run_json IS NOT NULL AND trim(g.run_json) NOT IN ('', '{}')
            """,
            (workflow,),
        ).fetchone()[0]
    )

    rated_rows = rated_runs(conn, workflow)
    profile = build_profile(conn, workflow) if rated_rows else None
    settings = (profile or {}).get("settings") or {}

    blockers: List[str] = []
    if total == 0:
        blockers.append("No generations in DB — run batch, then Sync & rate.")
    elif with_run == 0:
        blockers.append("Generations exist but run_json is empty — learning cannot read prompts/settings.")
    if rated == 0:
        blockers.append("No ratings yet — rate at least one output.")
    elif usable_rated == 0:
        blockers.append("Ratings exist but rated rows have no run_json — re-sync after updating trainer.")

    stage = "idle"
    if total > 0 and with_run == 0:
        stage = "sync_needed"
    elif rated == 0:
        stage = "rating"
    elif profile is None:
        stage = "blocked"
    elif settings:
        stage = "learning"
    else:
        stage = "profile_empty"

    return {
        "workflow": workflow,
        "stage": stage,
        "learning_active": bool(profile and settings),
        "generations_total": total,
        "generations_with_run_json": with_run,
        "ratings_count": rated,
        "rated_with_run_json": usable_rated,
        "profile_ready": profile is not None,
        "settings_learned": sorted(settings.keys()),
        "settings_count": len(settings),
        "profile": profile,
        "blockers": blockers,
        "auto_apply_on_rate": False,
    }


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
