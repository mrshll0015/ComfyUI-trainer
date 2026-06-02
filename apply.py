from __future__ import annotations

import json
import os
from copy import deepcopy
from typing import Any, Dict, Optional

from .prompts_store import set_action


def apply_profile_to_workflow(
    workflow_path: str,
    profile: Dict[str, Any],
    *,
    explore: bool = False,
    prompt_profile: str = "prompt_1",
) -> Dict[str, Any]:
    from .learn import mutate_profile

    if explore:
        profile = mutate_profile(profile)

    settings = profile.get("settings") or {}

    text_updated = 0
    if isinstance(settings.get("action"), str) and settings["action"].strip():
        set_action(prompt_profile, settings["action"].strip())
        text_updated = 1

    # Numeric sampler settings → workflow file
    updated = 0
    if os.path.isfile(workflow_path):
        with open(workflow_path, encoding="utf-8") as f:
            wf = json.load(f)

        for node in wf.get("nodes", []):
            class_type = node.get("type", "")

            if class_type == "KSampler":
                wv = node.setdefault("widgets_values", [])
                if "seed" in settings and len(wv) >= 1:
                    wv[0] = settings["seed"]
                    updated += 1
                if "steps" in settings and len(wv) >= 3:
                    wv[2] = settings["steps"]
                    updated += 1
                if "cfg" in settings and len(wv) >= 4:
                    wv[3] = settings["cfg"]
                    updated += 1

            if class_type == "WanImageToVideo" and "frames" in settings:
                wv = node.setdefault("widgets_values", [])
                if wv:
                    wv[0] = settings["frames"]
                    updated += 1

            if class_type == "VHS_VideoCombine" and "fps" in settings:
                wv = node.get("widgets_values")
                if isinstance(wv, dict) and "frame_rate" in wv:
                    wv["frame_rate"] = settings["fps"]
                    updated += 1

        with open(workflow_path, "w", encoding="utf-8") as f:
            json.dump(wf, f, indent=2, ensure_ascii=False)
            f.write("\n")

    return {
        "workflow_path": workflow_path,
        "prompt_profile": prompt_profile,
        "text_fields_updated": text_updated,
        "sampler_fields_updated": updated,
        "settings_applied": deepcopy(settings),
        "explore": explore,
    }
