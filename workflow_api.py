from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Tuple


WIDGET_INPUT_TYPES = {
    "INT",
    "FLOAT",
    "STRING",
    "BOOLEAN",
    "COMBO",
    "combo",
}


def _input_name_and_type(spec: Any) -> Tuple[str, str]:
    if isinstance(spec, (list, tuple)) and spec:
        return str(spec[0]), str(spec[1]) if len(spec) > 1 else "*"
    return str(spec), "*"


def _ordered_inputs(class_type: str, object_info: Dict[str, Any]) -> List[Tuple[str, str]]:
    info = object_info.get(class_type) or {}
    inp = info.get("input") or {}
    ordered: List[Tuple[str, str]] = []
    for section in ("required", "optional", "hidden"):
        block = inp.get(section) or {}
        for name, spec in block.items():
            ordered.append(_input_name_and_type(spec))
    return ordered


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


def build_api_prompt(
    workflow: Dict[str, Any],
    object_info: Dict[str, Any],
) -> Dict[str, Any]:
    nodes = {str(n["id"]): n for n in workflow.get("nodes", [])}

    links_by_target: Dict[Tuple[str, int], Tuple[str, int]] = {}
    for link in workflow.get("links", []):
        key = (str(link["target_id"]), int(link["target_slot"]))
        links_by_target[key] = (str(link["origin_id"]), int(link["origin_slot"]))

    prompt: Dict[str, Any] = {}

    for node_id, node in nodes.items():
        if node.get("mode") == 4:
            continue
        class_type = node["type"]
        if class_type not in object_info:
            raise KeyError(f"Unknown node type in ComfyUI: {class_type}")

        inputs: Dict[str, Any] = {}
        widget_values: List[Any] = []
        wv = node.get("widgets_values")
        if isinstance(wv, dict):
            widget_values = []
            dict_widgets = wv
        elif isinstance(wv, list):
            widget_values = list(wv)
            dict_widgets = None
        else:
            dict_widgets = None

        widget_idx = 0
        ordered = _ordered_inputs(class_type, object_info)

        for slot, (inp_name, inp_type) in enumerate(ordered):
            link_key = (node_id, slot)
            if link_key in links_by_target:
                src_id, src_slot = links_by_target[link_key]
                inputs[inp_name] = [src_id, src_slot]
            elif inp_type not in WIDGET_INPUT_TYPES and inp_type != "*":
                continue
            else:
                if dict_widgets is not None:
                    if inp_name in dict_widgets:
                        inputs[inp_name] = dict_widgets[inp_name]
                elif widget_idx < len(widget_values):
                    inputs[inp_name] = widget_values[widget_idx]
                    widget_idx += 1

        if dict_widgets is not None:
            for k, v in dict_widgets.items():
                inputs.setdefault(k, v)

        prompt[node_id] = {"class_type": class_type, "inputs": inputs}

    return prompt
