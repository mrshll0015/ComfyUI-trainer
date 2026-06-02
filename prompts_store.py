from __future__ import annotations

import json
import os
from copy import deepcopy
from typing import Any, Dict, Optional

# Workflow CLIPTextEncode node labels (filled from system + user action).
WORKFLOW_NODE_LABELS = (
    "Positive inpaint 1",
    "Negative inpaint 1",
    "Positive inpaint 2 lower body",
    "Negative inpaint 2",
    "Video motion prompt",
    "Video negative",
    "Skin tone prompt (photo + inpaint)",
    "Skin tone prompt (video)",
)

SYSTEM_TO_NODE = {
    "positive_inpaint_1": "Positive inpaint 1",
    "negative_inpaint_1": "Negative inpaint 1",
    "positive_inpaint_2": "Positive inpaint 2 lower body",
    "negative_inpaint_2": "Negative inpaint 2",
    "video_negative": "Video negative",
    "skin_tone_photo": "Skin tone prompt (photo + inpaint)",
    "skin_tone_video": "Skin tone prompt (video)",
}

NODE_TO_SYSTEM = {v: k for k, v in SYSTEM_TO_NODE.items()}

DEFAULT_SYSTEM: Dict[str, str] = {
    "video_prefix": (
        "exact same person, exact same face identity, same facial features, same hairstyle, "
        "same room background, static camera, no camera movement, natural smooth motion, "
    ),
    "positive_inpaint_1": (
        "photorealistic, uncensored, explicit, exact same woman, exact same face, exact same person, "
        "one person only, single complete body, two arms two hands attached to shoulders, "
        "exact same body shape proportions and pose, exact same room background lighting camera angle, "
        "remove all clothes completely, fully nude naked female body, bare skin, natural female anatomy, "
        "single navel, two legs two feet, correct hips pelvis thighs knees, exposed breasts nipples areola on chest only, "
        "exposed pussy vulva labia centered on pelvis, no shirt no bra no pants no skirt no underwear, "
        "distinct lower body not copied from chest, natural skin pores, seamless inpaint, 8k"
    ),
    "positive_inpaint_2": (
        "photorealistic, same woman same pose same lighting background, inpaint lower body from waist to feet only, "
        "bare hips pelvis vulva labia inner thighs legs knees feet, flat abdomen single belly button, "
        "smooth natural skin on belly hips thighs legs, no breasts on stomach, no nipples below chest line, "
        "no duplicated torso, one continuous female body, correct lower body anatomy, seamless skin transition at waist"
    ),
    "negative_inpaint_1": (
        "different person, different face, face change, identity change, wrong face, clothes, dressed, shirt, pants, "
        "skirt, dress, bra, underwear, bikini, swimsuit, lingerie, fabric, textile, partially clothed, covered breasts, "
        "covered nipples, covered genitals, censored, censorship, mosaic, blur censor, black bar, text, watermark, logo, "
        "deformed, bad anatomy, blurry, cartoon, duplicate torso, cloned upper body, mirrored chest on hips, "
        "extra breasts on thighs, copied breasts on legs, breasts on abdomen, nipple on belly, nipple on thigh, "
        "second torso below waist, symmetrical body duplication, extra navel, multiple belly buttons, third breast on abdomen, "
        "fused legs, malformed pelvis, genitals on thigh, vertical body tiling, repeated torso pattern, extra arm, extra hand, "
        "floating hand, disconnected limb, multiple people, cropped body parts"
    ),
    "negative_inpaint_2": (
        "breasts on abdomen, nipple on belly, nipple on thigh, extra breast below chest, duplicated chest on hips, "
        "second torso, cloned upper body, torso tiling, extra arm, floating hand, disconnected limb, clothes, skirt, pants, "
        "fabric, deformed, bad anatomy, blurry"
    ),
    "video_negative": (
        "different person, different face, face change, identity change, wrong face, distorted face, different hairstyle, "
        "skin tone mismatch, face-body color mismatch, patchy skin tone, over-desaturated skin, overexposed skin, "
        "different room, camera pan, camera zoom, camera shake, low motion, frozen static image, blurry, deformed, "
        "text, watermark, logo, captions"
    ),
    "skin_tone_photo": (
        "natural skin tone matching face, warm undertone, consistent melanin, uniform skin color across full body, "
        "same complexion from face to torso to legs, realistic skin texture and pores"
    ),
    "skin_tone_video": (
        "matching skin tone from face to body, consistent undertone, consistent melanin tone, uniform skin color, "
        "same complexion throughout video"
    ),
}

DEFAULT_SHARED: Dict[str, str] = {
    "clipseg_upper": (
        "shirt, blouse, top, t-shirt, bra, sports bra, bikini top, jacket, coat, hoodie, sweater, cardigan, "
        "clothing on torso, fabric on chest, straps on shoulders"
    ),
    "clipseg_lower": (
        "pants, jeans, skirt, mini skirt, maxi skirt, black skirt, dress, shorts, underwear, panties, thong, "
        "bikini bottom, swimsuit, stockings, socks, leggings, waistband, fabric on hips thighs legs belly lower torso, "
        "clothing below waist"
    ),
    "clipseg_cleanup": (
        "remaining clothes, fabric, textile, bra, underwear, bikini, shirt, pants, dress, straps, lace, covered skin, "
        "clothing, swimsuit"
    ),
}

DEFAULT_TRAINING: Dict[str, int] = {
    "max_dimension": 384,
    "video_frames": 33,
}


def prompts_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts.json")


def _default_prompts() -> Dict[str, Any]:
    return {
        "shared": dict(DEFAULT_SHARED),
        "system": dict(DEFAULT_SYSTEM),
        "training": dict(DEFAULT_TRAINING),
        "prompt_1": {"label": "Profile 1", "action": DEFAULT_ACTION},
        "prompt_2": {"label": "Profile 2", "action": ""},
    }


def _strip_prefix(text: str, prefix: str) -> str:
    text = (text or "").strip()
    prefix = (prefix or "").strip()
    if prefix and text.lower().startswith(prefix.lower()):
        return text[len(prefix) :].strip(" ,")
    return text


def _migrate_legacy_profile(prof: Dict[str, Any], system: Dict[str, str]) -> None:
    nodes = prof.pop("nodes", None)
    if not isinstance(nodes, dict):
        return
    for label, sys_key in NODE_TO_SYSTEM.items():
        val = (nodes.get(label) or "").strip()
        if val and not (system.get(sys_key) or "").strip():
            system[sys_key] = val
    video = (nodes.get("Video motion prompt") or "").strip()
    if video and not (prof.get("action") or "").strip():
        prof["action"] = _strip_prefix(video, system.get("video_prefix", "")) or video


def _normalize(data: Dict[str, Any]) -> Dict[str, Any]:
    out = deepcopy(data)
    system = out.setdefault("system", {})
    for key, val in DEFAULT_SYSTEM.items():
        system.setdefault(key, val)
    shared = out.setdefault("shared", {})
    for key, val in DEFAULT_SHARED.items():
        shared.setdefault(key, val)
    training = out.setdefault("training", {})
    for key, val in DEFAULT_TRAINING.items():
        training.setdefault(key, int(val))

    for profile_key in ("prompt_1", "prompt_2"):
        prof = out.setdefault(profile_key, {"label": profile_key, "action": ""})
        _migrate_legacy_profile(prof, system)
        prof.setdefault("label", profile_key)
        prof.setdefault("action", "")

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


def get_action(profile_key: str) -> str:
    data = load_prompts()
    if profile_key not in data:
        raise KeyError(f"Unknown profile: {profile_key}")
    return str(data[profile_key].get("action") or "").strip()


def set_action(profile_key: str, action: str) -> None:
    data = load_prompts()
    if profile_key not in data:
        raise KeyError(f"Unknown profile: {profile_key}")
    data[profile_key]["action"] = action.strip()
    save_prompts(data)


def build_video_motion(action: str, system: Optional[Dict[str, str]] = None) -> str:
    sys = system or load_prompts().get("system") or {}
    prefix = (sys.get("video_prefix") or "").strip()
    action = (action or "").strip()
    if not action:
        return prefix.rstrip(", ")
    if prefix:
        joiner = "" if prefix.endswith((" ", ", ")) else ", "
        return f"{prefix.rstrip()}{joiner}{action}"
    return action


def expand_profile(profile_key: str, *, action_override: Optional[str] = None) -> Dict[str, str]:
    """Full workflow prompt texts = hidden system templates + user action line."""
    data = load_prompts()
    if profile_key not in data:
        raise KeyError(f"Unknown profile: {profile_key}")
    system = data.get("system") or {}
    action = (action_override if action_override is not None else data[profile_key].get("action") or "").strip()

    nodes: Dict[str, str] = {}
    for sys_key, label in SYSTEM_TO_NODE.items():
        if sys_key == "video_negative":
            nodes[label] = system.get(sys_key, "")
        else:
            nodes[label] = system.get(sys_key, "")

    nodes["Video motion prompt"] = build_video_motion(action, system)

    shared = data.get("shared") or {}
    for k, v in shared.items():
        nodes[f"shared:{k}"] = v

    return nodes


def get_training_settings() -> Dict[str, int]:
    data = load_prompts()
    training = data.get("training") or {}
    max_dim = int(training.get("max_dimension", DEFAULT_TRAINING["max_dimension"]))
    frames = int(training.get("video_frames", DEFAULT_TRAINING["video_frames"]))
    max_dim = max(256, min(640, (max_dim // 16) * 16))
    frames = max(17, min(81, frames))
    return {"max_dimension": max_dim, "video_frames": frames}


def get_profile(profile_key: str, *, action_override: Optional[str] = None) -> Dict[str, str]:
    return expand_profile(profile_key, action_override=action_override)
