from __future__ import annotations

import argparse
import json

from .apply import apply_profile_to_workflow
from .comfy import default_workflow_path
from .db import connect
from .learn import build_profile


def main() -> None:
    ap = argparse.ArgumentParser(description="Apply learned profile to ComfyUI workflow JSON")
    ap.add_argument("--db", default=None)
    ap.add_argument("--workflow", default="app-photo-video.json")
    ap.add_argument("--workflow-path", default=None)
    ap.add_argument("--explore", action="store_true")
    ap.add_argument("--json-only", action="store_true")
    args = ap.parse_args()

    conn = connect(args.db)
    profile = build_profile(conn, args.workflow)
    if not profile:
        raise SystemExit("No ratings yet. Use trainer web UI or trainer.rate first.")

    if args.json_only:
        print(json.dumps(profile, ensure_ascii=False, indent=2))
        return

    wf_path = args.workflow_path or default_workflow_path(args.workflow)
    apply_result = apply_profile_to_workflow(wf_path, profile, explore=args.explore)
    print(json.dumps(apply_result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
