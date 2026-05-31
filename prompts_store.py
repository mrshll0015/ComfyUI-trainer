from __future__ import annotations

import json
import os
from copy import deepcopy
from typing import Any, Dict, Optional

PROMPT_NODE_LABELS = (
    "Positive inpaint 1",
    "Negative inpaint 1",
    "Positive inpaint 2 lower body",
    "Negative inpaint 2",
    "Video motion prompt",
    "Video negative",
    "Skin tone prompt (photo + inpaint)",
    "Skin tone prompt (video)",
)

LEGACY_LABEL_MAP = {
    "CLIPTextEncode_16": "Negative inpaint 1",
    "CLIPTextEncode_95": "Negative inpaint 2",
    "CLIPTextEncode_38": "Video negative",
}


def prompts_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts.json")


def _default_prompts() -> Dict[str, Any]:
    empty_nodes = {label: "" for label in PROMPT_NODE_LABELS}
    return {
        "shared": {
            "clipseg_upper": "",
            "clipseg_lower": "",
            "clipseg_cleanup": "",
        },
        "prompt_1": {"label": "Prompt profile 1", "nodes": dict(empty_nodes)},
        "prompt_2": {"label": "Prompt profile 2", "nodes": dict(empty_nodes)},
    }


def _normalize(data: Dict[str, Any]) -> Dict[str, Any]:
    out = deepcopy(data)
    for profile_key in ("prompt_1", "prompt_2"):
        prof = out.setdefault(profile_key, {"label": profile_key, "nodes": {}})
        nodes = prof.setdefault("nodes", {})
        for old, new in LEGACY_LABEL_MAP.items():
            if old in nodes and new not in nodes:
                nodes[new] = nodes.pop(old)
        for label in PROMPT_NODE_LABELS:
            nodes.setdefault(label, "")
    out.setdefault("shared", {})
    for k in ("clipseg_upper", "clipseg_lower", "clipseg_cleanup"):
        out["shared"].setdefault(k, "")
    return out


def load_prompts() -> Dict[str, Any]:
    path = prompts_path()
    if not os.path.isfile(path):
        data = _default_prompts()
        save_prompts(data)
        return data
    with open(path, encoding="utf-8") as f:
        return _normalize(json.load(f))


def save_prompts(data: Dict[str, Any]) -> None:
    path = prompts_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(_normalize(data), f, indent=2, ensure_ascii=False)
        f.write("\n")


def get_profile(profile_key: str) -> Dict[str, str]:
    data = load_prompts()
    if profile_key not in data:
        raise KeyError(f"Unknown profile: {profile_key}")
    shared = data.get("shared") or {}
    nodes = dict(data[profile_key].get("nodes") or {})
    return {**{f"shared:{k}": v for k, v in shared.items()}, **nodes}


def update_profile(profile_key: str, nodes: Dict[str, str]) -> None:
    data = load_prompts()
    if profile_key not in data:
        raise KeyError(f"Unknown profile: {profile_key}")
    prof_nodes = data[profile_key].setdefault("nodes", {})
    for k, v in nodes.items():
        if k.startswith("shared:"):
            data.setdefault("shared", {})[k.split(":", 1)[1]] = v
        else:
            prof_nodes[k] = v
    save_prompts(data)
