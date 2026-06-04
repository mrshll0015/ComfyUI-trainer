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
from .db import Rating, count_pending, db_session, insert_rating, list_generations
from .generate import poll_batch, run_batch, upload_image_bytes
from .learn import build_profile, learn_status
from .prompts_store import get_profile, load_prompts, set_action
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


def _iter_upload_files(form: cgi.FieldStorage) -> List[cgi.FieldStorage]:
    items: List[cgi.FieldStorage] = []
    if not form.list:
        return items
    for key in form:
        if key != "file":
            continue
        val = form[key]
        if isinstance(val, list):
            items.extend(val)
        else:
            items.append(val)
    return items


def _rating_from_body(body: Dict[str, Any]) -> Rating:
    overall = clamp_int(int(body.get("overall", 5)), 1, 10)
    face = clamp_int(int(body.get("face", overall)), 1, 10)
    hands = clamp_int(int(body.get("hands", overall)), 1, 10)
    fingers = clamp_int(int(body.get("fingers", overall)), 1, 10)
    body_score = clamp_int(int(body.get("body", overall)), 1, 10)
    skin_tone = clamp_int(int(body.get("skin_tone", overall)), 1, 10)
    return Rating(
        overall=overall,
        face=face,
        hands=hands,
        fingers=fingers,
        body=body_score,
        skin_tone=skin_tone,
        identity=overall,
        motion=overall,
        lighting=overall,
        artifacts=overall,
        comment=str(body.get("comment", "")).strip(),
    )


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
                with db_session() as conn:
                    pending = count_pending(conn, workflow=DEFAULT_WORKFLOW)
                    learn = learn_status(conn, DEFAULT_WORKFLOW)
                return _json_response(
                    self,
                    200,
                    {
                        "ok": True,
                        "comfy_ui": ping(COMFY_HOST, COMFY_PORT),
                        "comfy_url": comfy_base_url(COMFY_HOST, COMFY_PORT),
                        "workflow": DEFAULT_WORKFLOW,
                        "pending": pending,
                        "learn": learn,
                    },
                )
            if path == "/api/stats":
                with db_session() as conn:
                    pending = count_pending(conn, workflow=qs.get("workflow", [DEFAULT_WORKFLOW])[0])
                return _json_response(self, 200, pending)
            if path == "/api/prompts":
                data = load_prompts()
                return _json_response(
                    self,
                    200,
                    {
                        "prompt_1": {"label": data["prompt_1"].get("label"), "action": data["prompt_1"].get("action", "")},
                        "prompt_2": {"label": data["prompt_2"].get("label"), "action": data["prompt_2"].get("action", "")},
                    },
                )
            if path == "/api/history":
                workflow = qs.get("workflow", [DEFAULT_WORKFLOW])[0]
                pending = qs.get("pending", ["0"])[0] == "1"
                media = qs.get("media", [""])[0]
                with db_session() as conn:
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
                with db_session() as conn:
                    profile = build_profile(conn, workflow)
                if not profile:
                    return _json_response(self, 404, {"error": "No ratings yet."})
                return _json_response(self, 200, profile)
            if path == "/api/learn/status":
                workflow = qs.get("workflow", [DEFAULT_WORKFLOW])[0]
                with db_session() as conn:
                    status = learn_status(conn, workflow)
                return _json_response(self, 200, status)
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
                for key in ("prompt_1", "prompt_2"):
                    if key in body and isinstance(body[key], dict) and "action" in body[key]:
                        set_action(key, str(body[key]["action"]))
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
                with db_session() as conn:
                    prompt_ids = None
                    since_ts = None
                    batch_id = body.get("batch_id")
                    if batch_id:
                        row = conn.execute(
                            "SELECT prompt_ids, created_at FROM batch_runs WHERE id = ?",
                            (int(batch_id),),
                        ).fetchone()
                        if row:
                            try:
                                prompt_ids = json.loads(row["prompt_ids"] or "[]")
                            except json.JSONDecodeError:
                                prompt_ids = None
                            since_ts = int(row["created_at"])
                    ids = sync_history_to_generations(
                        conn,
                        workflow=workflow,
                        host=COMFY_HOST,
                        port=COMFY_PORT,
                        workflow_path=default_workflow_path(workflow),
                        prompt_ids=prompt_ids,
                        since_ts=since_ts,
                    )
                    pending = count_pending(conn, workflow=workflow)
                    learn = learn_status(conn, workflow)
                return _json_response(
                    self,
                    200,
                    {
                        "synced": ids.get("synced", 0),
                        "generation_ids": ids.get("generation_ids", []),
                        "skipped_missing": ids.get("skipped_missing", []),
                        "pending": pending,
                        "learn": learn,
                    },
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
        file_items = _iter_upload_files(form)
        if not file_items:
            return _json_response(self, 400, {"error": "file field required"})
        if len(file_items) > 10:
            return _json_response(self, 400, {"error": "max 10 photos per upload"})
        if not ping(COMFY_HOST, COMFY_PORT):
            return _json_response(self, 503, {"error": "ComfyUI offline"})

        filenames: List[str] = []
        total_size = 0
        for item in file_items:
            if not item.file:
                continue
            raw = item.file.read()
            total_size += len(raw)
            orig = item.filename or "upload.png"
            ext = os.path.splitext(orig)[1].lower() or ".png"
            name = f"trainer_{uuid.uuid4().hex[:12]}{ext}"
            uploaded = upload_image_bytes(raw, name, host=COMFY_HOST, port=COMFY_PORT)
            filenames.append(uploaded)

        if not filenames:
            return _json_response(self, 400, {"error": "empty file(s)"})

        payload: Dict[str, Any] = {
            "filenames": filenames,
            "count": len(filenames),
            "size": total_size,
        }
        if len(filenames) == 1:
            payload["filename"] = filenames[0]
        return _json_response(self, 200, payload)

    def _handle_generate(self) -> None:
        body = _read_json(self)
        image_names = body.get("image_names")
        if not isinstance(image_names, list) or not image_names:
            legacy = body.get("image_name")
            image_names = [legacy] if legacy else []
        image_names = [str(n) for n in image_names if n][:10]
        if not image_names:
            return _json_response(self, 400, {"error": "image_names required (upload photos first)"})
        prompt_profile = body.get("prompt_profile", "prompt_1")
        if prompt_profile not in ("prompt_1", "prompt_2"):
            return _json_response(self, 400, {"error": "prompt_profile must be prompt_1 or prompt_2"})
        action = body.get("action")
        if action is not None:
            set_action(prompt_profile, str(action).strip())
        workflow = body.get("workflow", DEFAULT_WORKFLOW)
        if not ping(COMFY_HOST, COMFY_PORT):
            return _json_response(self, 503, {"error": "ComfyUI offline"})
        result = run_batch(
            workflow=workflow,
            image_names=image_names,
            prompt_profile=prompt_profile,
            action=str(action).strip() if action is not None else None,
            host=COMFY_HOST,
            port=COMFY_PORT,
        )
        return _json_response(self, 200, result)

    def _handle_rate(self) -> None:
        body = _read_json(self)
        gen_id = int(body["generation_id"])
        rating = _rating_from_body(body)
        with db_session() as conn:
            rid = insert_rating(conn, generation_id=gen_id, rating=rating)
            profile = build_profile(conn, DEFAULT_WORKFLOW)
            learn = learn_status(conn, DEFAULT_WORKFLOW)
            pending = count_pending(conn, workflow=DEFAULT_WORKFLOW)
        return _json_response(
            self,
            200,
            {
                "rating_id": rid,
                "profile": profile,
                "learn": learn,
                "applied": None,
                "pending": pending,
            },
        )

    def _handle_apply(self) -> None:
        body = _read_json(self)
        workflow = body.get("workflow", DEFAULT_WORKFLOW)
        explore = bool(body.get("explore", False))
        with db_session() as conn:
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
