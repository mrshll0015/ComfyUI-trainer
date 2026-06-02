from __future__ import annotations

import json
import random
import uuid
from typing import Any, Dict, List, Optional, Tuple

from .comfy import (
    _get_json,
    _post_json,
    comfy_base_url,
    default_workflow_path,
    fetch_history,
    sync_history_to_generations,
)
from .db import connect, insert_batch_run, update_batch_run
from .learn import build_profile, capture_run_json_from_workflow
from .prompts_store import get_profile, get_training_settings, load_prompts
from .workflow_api import build_api_prompt, convert_workflow_via_comfy, fetch_object_info


def default_input_dir(root: Optional[str] = None) -> str:
    import os

    base = root or __import__("os").environ.get("RUNPOD_ROOT", "/workspace/runpod-slim")
    return os.path.join(base, "ComfyUI", "input")


def upload_image_bytes(
    data: bytes,
    filename: str,
    *,
    host: str = "127.0.0.1",
    port: int = 8188,
) -> str:
    import io
    import urllib.request
    from urllib.parse import quote

    boundary = f"----trainer{uuid.uuid4().hex}"
    body = io.BytesIO()
    for part in (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="image"; filename="{filename}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n",
    ):
        body.write(part.encode())
    body.write(data)
    body.write(f"\r\n--{boundary}--\r\n".encode())

    req = urllib.request.Request(
        f"{comfy_base_url(host, port)}/upload/image",
        data=body.getvalue(),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    return result.get("name") or filename


def queue_prompt(
    api_prompt: Dict[str, Any],
    *,
    host: str = "127.0.0.1",
    port: int = 8188,
    client_id: Optional[str] = None,
) -> str:
    payload = {
        "prompt": api_prompt,
        "client_id": client_id or str(uuid.uuid4()),
    }
    result = _post_json(f"{comfy_base_url(host, port)}/prompt", payload, timeout=120)
    return str(result.get("prompt_id", ""))


def get_queue(host: str = "127.0.0.1", port: int = 8188) -> Dict[str, Any]:
    return _get_json(f"{comfy_base_url(host, port)}/queue")


def _learned_settings(workflow: str) -> Dict[str, Any]:
    conn = connect()
    profile = build_profile(conn, workflow)
    return (profile or {}).get("settings") or {}


def _prepare_workflow_dict(
    workflow_path: str,
    *,
    image_name: str,
    prompt_profile: str,
    learned: Optional[Dict[str, Any]] = None,
    seed: Optional[int] = None,
    action: Optional[str] = None,
) -> Dict[str, Any]:
    with open(workflow_path, encoding="utf-8") as f:
        wf = json.load(f)

    profile_nodes = get_profile(prompt_profile, action_override=action)
    prompts_data = load_prompts()
    shared = prompts_data.get("shared") or {}
    training = get_training_settings()
    max_dim = training["max_dimension"]
    video_frames = training["video_frames"]

    clipseg_by_id = {48: "clipseg_upper", 69: "clipseg_lower", 73: "clipseg_cleanup"}

    for node in wf.get("nodes", []):
        class_type = node.get("type", "")
        label = (node.get("properties") or {}).get("Node name for S&R", "")

        if class_type == "LoadImage":
            wv = node.setdefault("widgets_values", ["", "image"])
            wv[0] = image_name

        elif class_type == "ImageScaleToMaxDimension":
            wv = node.setdefault("widgets_values", ["lanczos", max_dim])
            if len(wv) >= 2:
                wv[1] = max_dim

        elif class_type == "MathExpression|pysssss":
            wv = node.setdefault("widgets_values", [f"(min(a, {max_dim})//16)*16"])
            if wv:
                wv[0] = f"(min(a, {max_dim})//16)*16"

        elif class_type == "WanImageToVideo":
            wv = node.setdefault("widgets_values", [video_frames, 1])
            frames = video_frames
            if learned and "frames" in learned:
                frames = int(learned["frames"])
            if wv:
                wv[0] = frames

        elif class_type == "CLIPTextEncode" and label:
            text = profile_nodes.get(label, "")
            if text:
                node.setdefault("widgets_values", [""])[0] = text

        elif class_type == "CLIPSeg":
            key = clipseg_by_id.get(node.get("id"))
            if key and shared.get(key):
                node.setdefault("widgets_values", ["", 7, 0.2, 4])[0] = shared[key]

        elif class_type == "KSampler" and learned:
            wv = node.setdefault("widgets_values", [0, "randomize", 20, 8, "euler", "normal", 1])
            if seed is not None and len(wv) >= 1:
                wv[0] = seed
            if "steps" in learned and len(wv) >= 3:
                wv[2] = learned["steps"]
            if "cfg" in learned and len(wv) >= 4:
                wv[3] = learned["cfg"]

        elif class_type == "VHS_VideoCombine" and learned and "fps" in learned:
            wv = node.get("widgets_values")
            if isinstance(wv, dict):
                wv["frame_rate"] = learned["fps"]

    return wf


def run_batch(
    *,
    workflow: str,
    image_names: List[str],
    prompt_profile: str = "prompt_1",
    action: Optional[str] = None,
    host: str = "127.0.0.1",
    port: int = 8188,
) -> Dict[str, Any]:
    """Queue one video generation per input photo (max 10)."""
    if not image_names:
        raise ValueError("image_names required")
    image_names = list(image_names[:10])

    workflow_path = default_workflow_path(workflow)
    learned = _learned_settings(workflow)
    object_info = fetch_object_info(host=host, port=port)
    count = len(image_names)

    conn = connect()
    batch_id = insert_batch_run(
        conn,
        workflow=workflow,
        prompt_profile=prompt_profile,
        count=count,
        image_name=json.dumps(image_names),
    )

    prompt_ids: List[str] = []
    prompt_runs: Dict[str, Any] = {}
    errors: List[str] = []

    try:
        for i, image_name in enumerate(image_names):
            seed = random.randint(1, 2**31 - 1)
            wf = _prepare_workflow_dict(
                workflow_path,
                image_name=image_name,
                prompt_profile=prompt_profile,
                learned=learned,
                seed=seed,
                action=action,
            )
            run_json = capture_run_json_from_workflow(
                wf,
                prompt_profile=prompt_profile,
                source_image=image_name,
                action=action,
            )
            api_prompt = convert_workflow_via_comfy(wf, host=host, port=port)
            if api_prompt is None:
                api_prompt = build_api_prompt(wf, object_info)
            pid = queue_prompt(api_prompt, host=host, port=port)
            if pid:
                prompt_ids.append(pid)
                prompt_runs[pid] = run_json
            else:
                errors.append(f"photo {i + 1}: empty prompt_id")

        update_batch_run(
            conn,
            batch_id,
            status="queued",
            queued=len(prompt_ids),
            prompt_ids=prompt_ids,
            prompt_runs=prompt_runs,
            error="; ".join(errors) if errors else None,
        )
    except Exception as exc:
        update_batch_run(conn, batch_id, status="error", error=str(exc))
        raise

    return {
        "batch_id": batch_id,
        "queued": len(prompt_ids),
        "photos": len(image_names),
        "prompt_ids": prompt_ids,
        "image_names": image_names,
        "prompt_profile": prompt_profile,
        "errors": errors,
    }


def poll_batch(
    batch_id: int,
    *,
    workflow: str,
    host: str = "127.0.0.1",
    port: int = 8188,
    auto_sync: bool = True,
) -> Dict[str, Any]:
    conn = connect()
    row = conn.execute("SELECT * FROM batch_runs WHERE id = ?", (batch_id,)).fetchone()
    if not row:
        raise ValueError(f"batch {batch_id} not found")

    queue = get_queue(host=host, port=port)
    running = len(queue.get("queue_running") or [])
    pending = len(queue.get("queue_pending") or [])

    prompt_ids = json.loads(row["prompt_ids"] or "[]")
    history = fetch_history(host=host, port=port, max_items=128)
    done = sum(1 for pid in prompt_ids if pid in history)

    status = row["status"]
    if done >= row["count"]:
        status = "done"
    elif running or pending:
        status = "running"
    elif done > 0:
        status = "running"

    synced = 0
    if auto_sync and status == "done":
        synced = len(
            sync_history_to_generations(conn, workflow=workflow, host=host, port=port)
        )

    update_batch_run(conn, batch_id, status=status, completed=done, synced=synced)

    from .db import count_pending

    pending_stats = count_pending(conn, workflow=workflow)

    return {
        "batch_id": batch_id,
        "status": status,
        "queued": row["count"],
        "completed": done,
        "queue_running": running,
        "queue_pending": pending,
        "synced": synced,
        "pending_videos": pending_stats["videos"],
        "pending_total": pending_stats["total"],
    }
