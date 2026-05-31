from __future__ import annotations

import argparse
import json

from .db import connect
from .learn import build_profile, mutate_profile


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--workflow", required=True)
    ap.add_argument("--json-only", action="store_true", help="Print only suggested JSON")
    ap.add_argument("--explore", action="store_true", help="Add small random perturbation")
    args = ap.parse_args()

    conn = connect(args.db)
    profile = build_profile(conn, args.workflow)
    if not profile:
        raise SystemExit(
            "No ratings yet for this workflow. Rate at least one generation first:\n"
            "  python3 -m trainer.web   # browser UI on :8189\n"
            "  python3 -m trainer.rate --file <output> --workflow <name> --run-json <run_settings.json>"
        )

    suggestion = mutate_profile(profile) if args.explore else profile

    if args.json_only:
        print(json.dumps(suggestion, ensure_ascii=False, indent=2))
        return

    settings = suggestion.get("settings", {})
    print("Learned profile:")
    print(f"  ratings: {suggestion.get('rating_count')}")
    print(f"  best score: {suggestion.get('best_weighted_score')}  overall: {suggestion.get('best_overall')}")
    print("")
    print("Suggested settings for next run:")
    print(json.dumps(settings, ensure_ascii=False, indent=2))
    print("")
    print("Apply to workflow:")
    print(f"  python3 -m trainer.apply --workflow {args.workflow}")


if __name__ == "__main__":
    main()
