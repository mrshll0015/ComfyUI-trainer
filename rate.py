from __future__ import annotations

import argparse
import os
from typing import Optional

from .db import Rating, connect, insert_generation, insert_rating
from .util import clamp_int, load_json_file, sha256_file


def _ask_int(prompt: str, *, lo: int = 1, hi: int = 10, default: Optional[int] = None) -> int:
    while True:
        suffix = f" [{default}]" if default is not None else ""
        raw = input(f"{prompt} ({lo}-{hi}){suffix}: ").strip()
        if not raw and default is not None:
            return int(default)
        try:
            v = int(raw)
        except ValueError:
            print("Enter a number.")
            continue
        if lo <= v <= hi:
            return v
        print(f"Must be {lo}-{hi}.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None, help="Path to SQLite DB (default: trainer/ratings.sqlite)")
    ap.add_argument("--file", required=True, help="Path to generated image/video (output file)")
    ap.add_argument("--workflow", required=True, help="Workflow name (e.g. app-photo-video.json)")
    ap.add_argument("--run-json", default=None, help="JSON file with run settings (prompt/seed/steps/etc)")
    ap.add_argument("--notes", default="", help="Freeform notes about this generation")
    args = ap.parse_args()

    file_path = os.path.abspath(args.file)
    if not os.path.exists(file_path):
        raise SystemExit(f"File not found: {file_path}")

    print("Rate this generation. Higher = better.")
    overall = _ask_int("Overall")
    face = _ask_int("Face quality")
    identity = _ask_int("Identity match")
    hands = _ask_int("Hands")
    fingers = _ask_int("Fingers")
    body = _ask_int("Body anatomy")
    skin_tone = _ask_int("Skin tone")
    motion = _ask_int("Motion (if video; else rate pose realism)")
    lighting = _ask_int("Lighting consistency")
    artifacts = _ask_int("Artifacts (higher = fewer artifacts)")
    comment = input("Comment (optional): ").strip()

    rating = Rating(
        overall=clamp_int(overall, 1, 10),
        face=clamp_int(face, 1, 10),
        identity=clamp_int(identity, 1, 10),
        hands=clamp_int(hands, 1, 10),
        fingers=clamp_int(fingers, 1, 10),
        body=clamp_int(body, 1, 10),
        skin_tone=clamp_int(skin_tone, 1, 10),
        motion=clamp_int(motion, 1, 10),
        lighting=clamp_int(lighting, 1, 10),
        artifacts=clamp_int(artifacts, 1, 10),
        comment=comment,
    )

    run_json = load_json_file(args.run_json)
    file_hash = sha256_file(file_path)

    conn = connect(args.db)
    gen_id = insert_generation(
        conn,
        workflow=args.workflow,
        file_path=file_path,
        file_sha256=file_hash,
        run_json=run_json,
        notes=args.notes,
    )
    insert_rating(conn, generation_id=gen_id, rating=rating)
    print(f"Saved: generation_id={gen_id}")


if __name__ == "__main__":
    main()
