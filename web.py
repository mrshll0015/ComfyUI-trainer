from __future__ import annotations

import argparse
import cgi
import json
import mimetypes
import os
import traceback
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from .apply import apply_profile_to_workflow
from .comfy import (
    comfy_base_url,
    default_output_dir,
    default_workflow_path,
    media_view_url,
    ping,
    sync_history_to_generations,
)
from .db import Rating, connect, count_pending, insert_rating, list_generations
from .generate import poll_batch, run_batch, upload_image_bytes
from .learn import build_profile
from .prompts_store import get_profile, load_prompts, save_prompts, update_profile
from .util import clamp_int


STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
DEFAULT_WORKFLOW = os.environ.get("TRAINER_WORKFLOW", "app-photo-video.json")
COMFY_HOST = os.environ.get("COMFY_HOST", "127.0.0.1")
COMFY_PORT = int(os.environ.get("COMFY_PORT", "8188"))


def _json_response(handler: BaseHTTPRequestHandler, code: int, payload: Any) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(body)


def _read_json(handler: BaseHTTPRequestHandler) -> Dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0") or 0)
    raw = handler.rfile.read(length) if length else b"{}"
    return json.loads(raw.decode("utf-8") or "{}")


def _row_to_dict(row) -> Dict[str, Any]:
    return {k: row[k] for k in row.keys()}


class TrainerHandler(BaseHTTPRequestHandler):
    server_version = "ComfyTrainer/2.0"

    def log_message(self, fmt: str, *args) -> None:
        print(f"[trainer] {self.address_string()} {fmt % args}")

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        try:
            if path in ("/", "/index.html"):
                return self._serve_static("index.html")
            if path.startswith("/static/"):
                return self._serve_static(path[len("/static/") :])
            if path == "/api/health":
                conn = connect()
                pending = count_pending(conn, workflow=DEFAULT_WORKFLOW)
                return _json_response(
                    self,
                    200,
                    {
                        "ok": True,
                        "comfy_ui": ping(COMFY_HOST, COMFY_PORT),
                        "comfy_url": comfy_base_url(COMFY_HOST, COMFY_PORT),
                        "workflow": DEFAULT_WORKFLOW,
                        "pending": pending,
                    },
                )
            if path == "/api/stats":
                conn = connect()
                pending = count_pending(conn, workflow=qs.get("workflow", [DEFAULT_WORKFLOW])[0])
                return _json_response(self, 200, pending)
            if path == "/api/prompts":
                return _json_response(self, 200, load_prompts())
            if path == "/api/history":
                workflow = qs.get("workflow", [DEFAULT_WORKFLOW])[0]
                pending = qs.get("pending", ["0"])[0] == "1"
                media = qs.get("media", [""])[0]
                conn = connect()
                rows = list_generations(
                    conn,
                    workflow=workflow,
                    pending_only=pending,
                    limit=int(qs.get("limit", ["80"])[0]),
                )
                items = [_row_to_dict(r) for r in rows]
                if media == "video":
                    items = [i for i in items if (i.get("media_type") or "") == "video"]
                return _json_response(self, 200, {"items": items})
            if path == "/api/profile":
                workflow = qs.get("workflow", [DEFAULT_WORKFLOW])[0]
                conn = connect()
                profile = build_profile(conn, workflow)
                if not profile:
                    return _json_response(self, 404, {"error": "No ratings yet."})
                return _json_response(self, 200, profile)
            if path == "/api/generate/status":
                batch_id = int(qs.get("batch_id", ["0"])[0])
                workflow = qs.get("workflow", [DEFAULT_WORKFLOW])[0]
                result = poll_batch(batch_id, workflow=workflow, host=COMFY_HOST, port=COMFY_PORT)
                return _json_response(self, 200, result)
            if path == "/api/media":
                return self._serve_media(qs)
            if path == "/api/comfy-proxy":
                return self._proxy_comfy_view(qs)
            return _json_response(self, 404, {"error": "Not found"})
        except Exception as exc:
            traceback.print_exc()
            return _json_response(self, 500, {"error": str(exc)})

    def do_PUT(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/prompts":
                body = _read_json(self)
                save_prompts(body)
                return _json_response(self, 200, {"ok": True})
            return _json_response(self, 404, {"error": "Not found"})
        except Exception as exc:
            traceback.print_exc()
            return _json_response(self, 500, {"error": str(exc)})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/upload":
                return self._handle_upload()
            if parsed.path == "/api/generate":
                return self._handle_generate()
            if parsed.path == "/api/sync":
                body = _read_json(self)
                workflow = body.get("workflow", DEFAULT_WORKFLOW)
                conn = connect()
                ids = sync_history_to_generations(
                    conn,
                    workflow=workflow,
                    host=COMFY_HOST,
                    port=COMFY_PORT,
                )
                pending = count_pending(conn, workflow=workflow)
                return _json_response(
                    self,
                    200,
                    {"synced": len(ids), "generation_ids": ids, "pending": pending},
                )
            if parsed.path == "/api/rate":
                return self._handle_rate()
            if parsed.path == "/api/apply":
                return self._handle_apply()
            return _json_response(self, 404, {"error": "Not found"})
        except Exception as exc:
            traceback.print_exc()
            return _json_response(self, 500, {"error": str(exc)})

    def _serve_static(self, name: str) -> None:
        safe = os.path.normpath(name).lstrip("/")
        file_path = os.path.join(STATIC_DIR, safe)
        if not file_path.startswith(STATIC_DIR) or not os.path.isfile(file_path):
            return _json_response(self, 404, {"error": "Static file not found"})
        mime, _ = mimetypes.guess_type(file_path)
        with open(file_path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", mime or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_media(self, qs: Dict[str, list]) -> None:
        rel = qs.get("path", [""])[0]
        if not rel:
            return _json_response(self, 400, {"error": "path required"})
        output_dir = os.path.abspath(default_output_dir())
        file_path = os.path.abspath(os.path.join(output_dir, rel))
        if not file_path.startswith(output_dir) or not os.path.isfile(file_path):
            return _json_response(self, 404, {"error": "File not found"})
        mime, _ = mimetypes.guess_type(file_path)
        with open(file_path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", mime or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _proxy_comfy_view(self, qs: Dict[str, list]) -> None:
        filename = qs.get("filename", [""])[0]
        subfolder = qs.get("subfolder", [""])[0]
        media_type = qs.get("type", ["output"])[0]
        if not filename:
            return _json_response(self, 400, {"error": "filename required"})
        url = media_view_url(
            filename,
            subfolder=subfolder,
            media_type=media_type,
            host=COMFY_HOST,
            port=COMFY_PORT,
        )
        import urllib.request

        with urllib.request.urlopen(url, timeout=30) as resp:
            data = resp.read()
            ctype = resp.headers.get("Content-Type", "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle_upload(self) -> None:
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            return _json_response(self, 400, {"error": "multipart/form-data required"})
        environ = {
            "REQUEST_METHOD": "POST",
            "CONTENT_TYPE": content_type,
        }
        form = cgi.FieldStorage(fp=self.rfile, headers=self.headers, environ=environ)
        if "file" not in form:
            return _json_response(self, 400, {"error": "file field required"})
        item = form["file"]
        if not item.file:
            return _json_response(self, 400, {"error": "empty file"})
        raw = item.file.read()
        orig = item.filename or "upload.png"
        ext = os.path.splitext(orig)[1].lower() or ".png"
        name = f"trainer_{uuid.uuid4().hex[:12]}{ext}"
        if not ping(COMFY_HOST, COMFY_PORT):
            return _json_response(self, 503, {"error": "ComfyUI offline"})
        uploaded = upload_image_bytes(raw, name, host=COMFY_HOST, port=COMFY_PORT)
        return _json_response(self, 200, {"filename": uploaded, "size": len(raw)})

    def _handle_generate(self) -> None:
        body = _read_json(self)
        image_name = body.get("image_name")
        if not image_name:
            return _json_response(self, 400, {"error": "image_name required (upload photo first)"})
        prompt_profile = body.get("prompt_profile", "prompt_1")
        if prompt_profile not in ("prompt_1", "prompt_2"):
            return _json_response(self, 400, {"error": "prompt_profile must be prompt_1 or prompt_2"})
        count = int(body.get("count", 10))
        count = max(1, min(10, count))
        workflow = body.get("workflow", DEFAULT_WORKFLOW)
        if not ping(COMFY_HOST, COMFY_PORT):
            return _json_response(self, 503, {"error": "ComfyUI offline"})
        result = run_batch(
            workflow=workflow,
            image_name=image_name,
            prompt_profile=prompt_profile,
            count=count,
            host=COMFY_HOST,
            port=COMFY_PORT,
        )
        return _json_response(self, 200, result)

    def _handle_rate(self) -> None:
        body = _read_json(self)
        gen_id = int(body["generation_id"])
        rating = Rating(
            overall=clamp_int(int(body.get("overall", 5)), 1, 10),
            face=clamp_int(int(body.get("face", 5)), 1, 10),
            identity=clamp_int(int(body.get("identity", 5)), 1, 10),
            hands=clamp_int(int(body.get("hands", 5)), 1, 10),
            fingers=clamp_int(int(body.get("fingers", 5)), 1, 10),
            body=clamp_int(int(body.get("body", 5)), 1, 10),
            skin_tone=clamp_int(int(body.get("skin_tone", 5)), 1, 10),
            motion=clamp_int(int(body.get("motion", 5)), 1, 10),
            lighting=clamp_int(int(body.get("lighting", 5)), 1, 10),
            artifacts=clamp_int(int(body.get("artifacts", 5)), 1, 10),
            comment=str(body.get("comment", "")).strip(),
        )
        conn = connect()
        rid = insert_rating(conn, generation_id=gen_id, rating=rating)
        profile = build_profile(conn, DEFAULT_WORKFLOW)
        apply_result = None
        if profile:
            wf_path = default_workflow_path(DEFAULT_WORKFLOW)
            apply_result = apply_profile_to_workflow(wf_path, profile, explore=False)
        pending = count_pending(conn, workflow=DEFAULT_WORKFLOW)
        return _json_response(
            self,
            200,
            {"rating_id": rid, "profile": profile, "applied": apply_result, "pending": pending},
        )

    def _handle_apply(self) -> None:
        body = _read_json(self)
        workflow = body.get("workflow", DEFAULT_WORKFLOW)
        explore = bool(body.get("explore", False))
        conn = connect()
        profile = build_profile(conn, workflow)
        if not profile:
            return _json_response(self, 400, {"error": "No learned profile yet."})
        wf_path = body.get("workflow_path") or default_workflow_path(workflow)
        result = apply_profile_to_workflow(wf_path, profile, explore=explore)
        return _json_response(self, 200, {"profile": profile, "result": result})


def main() -> None:
    global DEFAULT_WORKFLOW

    ap = argparse.ArgumentParser(description="ComfyUI generation trainer")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=int(os.environ.get("TRAINER_PORT", "8189")))
    ap.add_argument("--workflow", default=DEFAULT_WORKFLOW)
    args = ap.parse_args()

    DEFAULT_WORKFLOW = args.workflow

    server = ThreadingHTTPServer((args.host, args.port), TrainerHandler)
    print(f"Trainer UI: http://{args.host}:{args.port}/")
    print(f"Workflow: {DEFAULT_WORKFLOW}")
    print(f"ComfyUI: {comfy_base_url(COMFY_HOST, COMFY_PORT)}")
    server.serve_forever()


if __name__ == "__main__":
    main()
