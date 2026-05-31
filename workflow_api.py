from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


WIDGET_INPUT_TYPES = {
    "INT",
    "FLOAT",
    "STRING",
    "BOOLEAN",
    "COMBO",
    "combo",
}

_CONTROL_AFTER_GENERATE = {"fixed", "increment", "decrement", "randomize"}


def _input_name_and_type(spec: Any) -> Tuple[str, str]:
    if isinstance(spec, (list, tuple)) and spec:
        return str(spec[0]), str(spec[1]) if len(spec) > 1 else "*"
    return str(spec), "*"


def _is_widget_input_spec(spec: Any) -> bool:
    if not isinstance(spec, (list, tuple)) or not spec:
        return False
    input_type = spec[0]
    if isinstance(input_type, (list, tuple)):
        return True
    if input_type in WIDGET_INPUT_TYPES:
        return True
    if isinstance(input_type, str) and input_type.startswith("COMFY_") and "COMBO" in input_type:
        return True
    if isinstance(input_type, str) and not input_type.isupper():
        return True
    return False


def _widget_names(class_type: str, object_info: Dict[str, Any]) -> List[str]:
    info = object_info.get(class_type) or {}
    inp = info.get("input") or {}
    names: List[str] = []
    for section in ("required", "optional", "hidden"):
        block = inp.get(section) or {}
        for name, spec in block.items():
            if _is_widget_input_spec(spec):
                names.append(name)
    return names


def _widget_default(spec: Any) -> Any:
    if not isinstance(spec, (list, tuple)) or not spec:
        return None
    input_type = spec[0]
    options = spec[1] if len(spec) > 1 else None

    if isinstance(input_type, (list, tuple)) and input_type:
        return input_type[0]

    if isinstance(options, dict):
        if "default" in options:
            return options["default"]
        combo_options = options.get("options")
        if input_type == "COMBO" and isinstance(combo_options, list) and combo_options:
            return combo_options[0]

    if input_type == "INT":
        return 0
    if input_type == "FLOAT":
        return 0.0
    if input_type == "STRING":
        return ""
    if input_type == "BOOLEAN":
        return False
    return None


def _fill_missing_widget_defaults(
    class_type: str,
    object_info: Dict[str, Any],
    inputs: Dict[str, Any],
) -> None:
    info = object_info.get(class_type) or {}
    inp = info.get("input") or {}
    for section in ("required", "optional", "hidden"):
        block = inp.get(section) or {}
        for name, spec in block.items():
            if name in inputs:
                continue
            if not _is_widget_input_spec(spec):
                continue
            default = _widget_default(spec)
            if default is not None:
                inputs[name] = default


def _filter_control_values(widget_values: List[Any]) -> List[Any]:
    filtered: List[Any] = []
    for i, value in enumerate(widget_values):
        if value in _CONTROL_AFTER_GENERATE:
            continue
        if (
            i + 1 < len(widget_values)
            and widget_values[i + 1] in _CONTROL_AFTER_GENERATE
        ):
            filtered.append(value)
            continue
        filtered.append(value)
    return filtered


def fetch_object_info(
    host: str = "127.0.0.1",
    port: int = 8188,
    *,
    cache: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if cache is not None:
        return cache
    from .comfy import _get_json, comfy_base_url

    return _get_json(f"{comfy_base_url(host, port)}/object_info", timeout=60.0)


def convert_workflow_via_comfy(
    workflow: Dict[str, Any],
    *,
    host: str = "127.0.0.1",
    port: int = 8188,
) -> Optional[Dict[str, Any]]:
    """Use ComfyUI /workflow/convert when the endpoint is available."""
    from .comfy import _post_json, comfy_base_url

    try:
        result = _post_json(
            f"{comfy_base_url(host, port)}/workflow/convert",
            workflow,
            timeout=120.0,
        )
    except Exception:
        return None
    if isinstance(result, dict) and result:
        return result
    return None


def build_api_prompt(
    workflow: Dict[str, Any],
    object_info: Dict[str, Any],
) -> Dict[str, Any]:
    """Convert ComfyUI workflow JSON (UI export) to API /prompt payload."""
    nodes = {str(n["id"]): n for n in workflow.get("nodes", [])}

    links_by_target: Dict[Tuple[str, int], Tuple[str, int]] = {}
    for link in workflow.get("links", []):
        key = (str(link["target_id"]), int(link["target_slot"]))
        links_by_target[key] = (str(link["origin_id"]), int(link["origin_slot"]))

    prompt: Dict[str, Any] = {}
    missing_types: List[str] = []

    for node_id, node in nodes.items():
        if node.get("mode") == 4:
            continue
        class_type = node["type"]
        if class_type not in object_info:
            missing_types.append(class_type)
            continue

        inputs: Dict[str, Any] = {}
        node_input_defs = node.get("inputs") or []

        for slot, inp_def in enumerate(node_input_defs):
            link_key = (node_id, slot)
            if link_key not in links_by_target:
                continue
            src_id, src_slot = links_by_target[link_key]
            inputs[inp_def["name"]] = [src_id, src_slot]

        wv = node.get("widgets_values")
        if isinstance(wv, dict):
            for key, value in wv.items():
                if key in {"videopreview", "preview"}:
                    continue
                inputs.setdefault(key, value)
        else:
            widget_names = _widget_names(class_type, object_info)
            filtered = _filter_control_values(list(wv or []))
            for i, name in enumerate(widget_names):
                if name in inputs:
                    continue
                if i < len(filtered):
                    inputs[name] = filtered[i]

        _fill_missing_widget_defaults(class_type, object_info, inputs)

        prompt[node_id] = {"class_type": class_type, "inputs": inputs}

    if missing_types:
        unique = sorted(set(missing_types))
        raise KeyError(
            "Unknown node types in ComfyUI (install missing custom nodes): "
            + ", ".join(unique)
        )

    return prompt
