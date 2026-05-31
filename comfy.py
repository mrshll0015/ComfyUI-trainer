from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple


def _get_json(url: str, timeout: float = 30.0) -> Any:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _post_json(url: str, payload: Dict[str, Any], timeout: float = 60.0) -> Any:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def comfy_base_url(host: str = "127.0.0.1", port: int = 8188) -> str:
    return f"http://{host}:{port}"


def _runpod_root(root: Optional[str] = None) -> str:
    return root or os.environ.get("RUNPOD_ROOT", "/workspace/runpod-slim")


def default_output_dir(root: Optional[str] = None) -> str:
    return os.path.join(_runpod_root(root), "ComfyUI", "output")


def resolve_workflow_path(name: str, root: Optional[str] = None) -> str:
    """Find workflow JSON on RunPod (trainer repo, ComfyUI user dir, or root)."""
    if os.path.isabs(name) and os.path.isfile(name):
        return name

    base = _runpod_root(root)
    cu = os.path.join(base, "ComfyUI")
    candidates = [
        os.path.join(base, "trainer", "workflows", name),
        os.path.join(base, "workflows", name),
        os.path.join(cu, "user", "default", "workflows", name),
        os.path.join(cu, "user", "workflows", name),
        os.path.join(base, name),
    ]
    env_path = os.environ.get("TRAINER_WORKFLOW_PATH")
    if env_path:
        candidates.insert(0, env_path)

    for path in candidates:
        if os.path.isfile(path):
            return path

    return candidates[0]


def default_workflow_path(name: str, root: Optional[str] = None) -> str:
    return resolve_workflow_path(name, root)


def ping(host: str = "127.0.0.1", port: int = 8188) -> bool:
    try:
        valid = _get_json(f"{comfy_base_url(host, port)}/system_stats", timeout=5.0)
        return isinstance(valid, dict)
    except Exception:
        return False


def fetch_history(
    host: str = "127.0.0.1",
    port: int = 8188,
    *,
    max_items: int = 64,
) -> Dict[str, Any]:
    return _get_json(f"{comfy_base_url(host, port)}/history?max_items={max_items}")


def media_view_url(
    filename: str,
    *,
    subfolder: str = "",
    media_type: str = "output",
    host: str = "127.0.0.1",
    port: int = 8188,
) -> str:
    q = urllib.parse.urlencode(
        {"filename": filename, "subfolder": subfolder, "type": media_type}
    )
    return f"{comfy_base_url(host, port)}/view?{q}"


def resolve_output_path(
    filename: str,
    *,
    subfolder: str = "",
    output_dir: Optional[str] = None,
) -> str:
    base = output_dir or default_output_dir()
    if subfolder:
        return os.path.join(base, subfolder, filename)
    return os.path.join(base, filename)


def _node_label(node: Dict[str, Any]) -> str:
    props = node.get("_meta", {}) or node.get("properties", {}) or {}
    return props.get("Node name for S&R") or props.get("title") or node.get("class_type", "")


def extract_run_settings(prompt: Dict[str, Any]) -> Dict[str, Any]:
    """Extract tunable settings from a ComfyUI API prompt dict."""
    run: Dict[str, Any] = {"nodes": {}}

    for node_id, node in prompt.items():
        if not isinstance(node, dict):
            continue
        class_type = node.get("class_type", "")
        inputs = node.get("inputs", {}) or {}
        label = _node_label(node)

        if class_type == "CLIPTextEncode":
            text = inputs.get("text")
            if isinstance(text, str) and text.strip():
                key = label or f"CLIPTextEncode_{node_id}"
                run["nodes"][key] = {"type": class_type, "text": text}

        elif class_type == "KSampler":
            run["nodes"][label or f"KSampler_{node_id}"] = {
                "type": class_type,
                "seed": inputs.get("seed"),
                "steps": inputs.get("steps"),
                "cfg": inputs.get("cfg"),
                "sampler_name": inputs.get("sampler_name"),
                "scheduler": inputs.get("scheduler"),
                "denoise": inputs.get("denoise"),
            }
            if isinstance(inputs.get("steps"), int):
                run.setdefault("steps", inputs["steps"])
            if isinstance(inputs.get("cfg"), (int, float)):
                run.setdefault("cfg", float(inputs["cfg"]))
            if isinstance(inputs.get("seed"), int):
                run.setdefault("seed", inputs["seed"])

        elif class_type == "WanImageToVideo":
            run["nodes"][label or f"WanImageToVideo_{node_id}"] = {
                "type": class_type,
                "length": inputs.get("length"),
                "batch_size": inputs.get("batch_size"),
            }
            if isinstance(inputs.get("length"), int):
                run["frames"] = inputs["length"]

        elif class_type == "VHS_VideoCombine":
            run["nodes"][label or f"VHS_VideoCombine_{node_id}"] = {
                "type": class_type,
                "frame_rate": inputs.get("frame_rate"),
            }
            if isinstance(inputs.get("frame_rate"), (int, float)):
                run["fps"] = int(inputs["frame_rate"])

    for key in (
        "Positive inpaint 1",
        "Negative inpaint 1",
        "Positive inpaint 2 lower body",
        "Negative inpaint 2",
        "Video motion prompt",
        "Video negative",
        "Skin tone prompt (photo + inpaint)",
        "Skin tone prompt (video)",
    ):
        node = run["nodes"].get(key)
        if node and node.get("text"):
            slug = key.lower().replace(" ", "_").replace("+", "plus").replace("(", "").replace(")", "")
            run[slug] = node["text"]

    return run


def _collect_outputs(entry: Dict[str, Any]) -> List[Tuple[str, str, str]]:
    """Return list of (filename, subfolder, media_type)."""
    found: List[Tuple[str, str, str]] = []
    outputs = entry.get("outputs") or {}
    for _nid, out in outputs.items():
        for kind, media_type in (("images", "image"), ("gifs", "video"), ("videos", "video")):
            for item in out.get(kind) or []:
                filename = item.get("filename")
                if filename:
                    found.append((filename, item.get("subfolder") or "", media_type))
    return found


def sync_history_to_generations(
    conn,
    *,
    workflow: str,
    host: str = "127.0.0.1",
    port: int = 8188,
    output_dir: Optional[str] = None,
) -> List[int]:
    from .db import insert_generation
    from .util import sha256_file

    history = fetch_history(host=host, port=port)
    created: List[int] = []
    out_dir = output_dir or default_output_dir()

    for prompt_id, entry in history.items():
        prompt = entry.get("prompt") or []
        prompt_dict = prompt[2] if len(prompt) >= 3 and isinstance(prompt[2], dict) else {}
        run_json = extract_run_settings(prompt_dict) if prompt_dict else None

        for filename, subfolder, media_type in _collect_outputs(entry):
            file_path = resolve_output_path(filename, subfolder=subfolder, output_dir=out_dir)
            if not os.path.exists(file_path):
                continue
            file_hash = sha256_file(file_path)
            gen_id = insert_generation(
                conn,
                workflow=workflow,
                file_path=file_path,
                file_sha256=file_hash,
                run_json=run_json,
                notes=f"synced from ComfyUI history",
                prompt_id=f"{prompt_id}:{filename}",
                media_type=media_type,
            )
            created.append(gen_id)
    return created
