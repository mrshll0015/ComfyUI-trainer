from __future__ import annotations

import json
import os
import time
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
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        message = raw or str(exc)
        try:
            body = json.loads(raw)
            err = body.get("error") or {}
            if isinstance(err, dict) and err.get("message"):
                message = str(err["message"])
            elif body.get("message"):
                message = str(body["message"])
            node_errors = body.get("node_errors")
            if node_errors:
                parts: List[str] = []
                for nid, detail in node_errors.items():
                    if not isinstance(detail, dict):
                        continue
                    for e in detail.get("errors") or []:
                        if isinstance(e, dict) and e.get("message"):
                            parts.append(f"node {nid}: {e['message']}")
                        elif isinstance(e, str):
                            parts.append(f"node {nid}: {e}")
                if parts:
                    message = f"{message} ({'; '.join(parts[:3])})"
        except json.JSONDecodeError:
            pass
        raise RuntimeError(f"HTTP Error {exc.code}: {message}") from exc


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


_VIDEO_EXTENSIONS = {".mp4", ".webm", ".gif", ".mov", ".mkv", ".avi"}


def resolve_output_path(
    filename: str,
    *,
    subfolder: str = "",
    output_dir: Optional[str] = None,
) -> str:
    found = find_output_file(filename, subfolder=subfolder, output_dir=output_dir)
    return found or os.path.join(
        output_dir or default_output_dir(),
        subfolder,
        filename,
    ) if subfolder else os.path.join(output_dir or default_output_dir(), filename)


def find_output_file(
    filename: str,
    *,
    subfolder: str = "",
    output_dir: Optional[str] = None,
) -> Optional[str]:
    base = output_dir or default_output_dir()
    candidates: List[str] = []
    if subfolder:
        candidates.append(os.path.join(base, subfolder, filename))
    candidates.append(os.path.join(base, filename))
    for path in candidates:
        if os.path.isfile(path):
            return path
    for root, _dirs, files in os.walk(base):
        if filename in files:
            return os.path.join(root, filename)
    return None


def _media_type_for_file(filename: str, default: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    if ext in _VIDEO_EXTENSIONS:
        return "video"
    return default


def _collect_outputs(entry: Dict[str, Any]) -> List[Tuple[str, str, str, Optional[str]]]:
    """Return list of (filename, subfolder, media_type, fullpath)."""
    found: List[Tuple[str, str, str, Optional[str]]] = []
    seen: set = set()
    outputs = entry.get("outputs") or {}
    for _nid, out in outputs.items():
        if not isinstance(out, dict):
            continue
        for kind, default_type in (
            ("images", "image"),
            ("gifs", "video"),
            ("videos", "video"),
            ("animated", "video"),
        ):
            for item in out.get(kind) or []:
                if not isinstance(item, dict):
                    continue
                filename = item.get("filename")
                if not filename:
                    continue
                key = (filename, item.get("subfolder") or "")
                if key in seen:
                    continue
                seen.add(key)
                media_type = _media_type_for_file(filename, default_type)
                fullpath = item.get("fullpath") or item.get("filepath")
                if isinstance(fullpath, str):
                    fullpath = fullpath.strip() or None
                else:
                    fullpath = None
                found.append((filename, item.get("subfolder") or "", media_type, fullpath))
    return found


def _scan_recent_videos(
    output_dir: str,
    since_ts: int,
    *,
    limit: int = 20,
) -> List[str]:
    matches: List[Tuple[float, str]] = []
    for root, _dirs, files in os.walk(output_dir):
        for name in files:
            ext = os.path.splitext(name)[1].lower()
            if ext not in _VIDEO_EXTENSIONS:
                continue
            path = os.path.join(root, name)
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            if mtime >= since_ts - 120:
                matches.append((mtime, path))
    matches.sort(reverse=True)
    return [path for _mtime, path in matches[:limit]]


def sync_history_to_generations(
    conn,
    *,
    workflow: str,
    host: str = "127.0.0.1",
    port: int = 8188,
    output_dir: Optional[str] = None,
    workflow_path: Optional[str] = None,
    prompt_ids: Optional[List[str]] = None,
    since_ts: Optional[int] = None,
) -> Dict[str, Any]:
    from .db import insert_generation, lookup_run_json_for_prompt
    from .util import sha256_file

    try:
        history = fetch_history(host=host, port=port, max_items=256)
    except Exception:
        history = {}
    created: List[int] = []
    skipped_missing: List[str] = []
    out_dir = output_dir or default_output_dir()

    wf: Optional[Dict[str, Any]] = None
    wf_path = workflow_path or default_workflow_path(workflow)
    if os.path.isfile(wf_path):
        with open(wf_path, encoding="utf-8") as f:
            wf = json.load(f)

    history_items = history.items()
    if prompt_ids:
        wanted = set(prompt_ids)
        history_items = [(pid, entry) for pid, entry in history.items() if pid in wanted]

    synced_paths: set = set()

    for prompt_id, entry in history_items:
        prompt = entry.get("prompt") or []
        prompt_dict = prompt[2] if len(prompt) >= 3 and isinstance(prompt[2], dict) else {}
        run_json = lookup_run_json_for_prompt(conn, prompt_id)
        if run_json is None and prompt_dict:
            run_json = extract_run_settings(prompt_dict, workflow=wf)

        outputs = _collect_outputs(entry)
        if not outputs and since_ts:
            continue

        for filename, subfolder, media_type, fullpath in outputs:
            file_path: Optional[str] = None
            if fullpath and os.path.isfile(fullpath):
                file_path = fullpath
            else:
                file_path = find_output_file(filename, subfolder=subfolder, output_dir=out_dir)
            if not file_path:
                skipped_missing.append(filename)
                continue
            if file_path in synced_paths:
                continue
            synced_paths.add(file_path)
            file_hash = sha256_file(file_path)
            gen_id = insert_generation(
                conn,
                workflow=workflow,
                file_path=file_path,
                file_sha256=file_hash,
                run_json=run_json,
                notes="synced from ComfyUI history",
                prompt_id=f"{prompt_id}:{filename}",
                media_type=media_type,
            )
            created.append(gen_id)

    scan_since = since_ts if since_ts is not None else int(time.time()) - 86400
    need_scan = len(created) < (len(prompt_ids) if prompt_ids else 1)
    if scan_since and (need_scan or prompt_ids):
        for path in _scan_recent_videos(out_dir, scan_since, limit=30):
            if path in synced_paths:
                continue
            name = os.path.basename(path)
            prompt_id = prompt_ids[0] if prompt_ids and len(prompt_ids) == 1 else "scan"
            pid_key = f"{prompt_id}:{name}"
            existing = conn.execute(
                "SELECT id FROM generations WHERE prompt_id = ? OR file_path = ?",
                (pid_key, path),
            ).fetchone()
            if existing:
                created.append(int(existing["id"]))
                synced_paths.add(path)
                continue
            run_json = lookup_run_json_for_prompt(conn, prompt_ids[0]) if prompt_ids else None
            gen_id = insert_generation(
                conn,
                workflow=workflow,
                file_path=path,
                file_sha256=sha256_file(path),
                run_json=run_json,
                notes="synced from output folder scan",
                prompt_id=pid_key,
                media_type="video",
            )
            created.append(gen_id)
            synced_paths.add(path)

    return {
        "generation_ids": created,
        "synced": len(created),
        "skipped_missing": skipped_missing,
    }


def _node_label(node: Dict[str, Any]) -> str:
    props = node.get("_meta", {}) or node.get("properties", {}) or {}
    return props.get("Node name for S&R") or props.get("title") or node.get("class_type", "")


def extract_run_settings(
    prompt: Dict[str, Any],
    workflow: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Extract tunable settings from a ComfyUI API prompt dict."""
    id_to_label: Dict[str, str] = {}
    if workflow:
        for node in workflow.get("nodes", []):
            label = _node_label(node)
            if label:
                id_to_label[str(node["id"])] = label

    run: Dict[str, Any] = {"nodes": {}}

    for node_id, node in prompt.items():
        if not isinstance(node, dict):
            continue
        class_type = node.get("class_type", "")
        inputs = node.get("inputs", {}) or {}
        label = id_to_label.get(str(node_id)) or _node_label(node) or f"{class_type}_{node_id}"

        if class_type == "CLIPTextEncode":
            text = inputs.get("text")
            if isinstance(text, str) and text.strip():
                run["nodes"][label] = {"type": class_type, "text": text}

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

    video_node = run["nodes"].get("Video motion prompt")
    if video_node and video_node.get("text"):
        from .prompts_store import load_prompts, _strip_prefix

        prefix = (load_prompts().get("system") or {}).get("video_prefix", "")
        action = _strip_prefix(video_node["text"], prefix)
        if action:
            run["action"] = action

    return run
