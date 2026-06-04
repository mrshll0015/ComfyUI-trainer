#!/bin/bash
# RunPod: cd /workspace/runpod-slim/trainer && bash pull-code-only.sh
# Updates Python/UI code only; keeps ratings, prompts, applied workflow.
set -e
cd "$(dirname "$0")"

PROTECT=(
  "prompts.json"
  "workflows/app-photo-video.json"
  "ratings.sqlite"
)

# Backup before first pull after repo migration (prompts/workflow removed from git)
for f in prompts.json workflows/app-photo-video.json; do
  if [ -f "$f" ] && [ ! -f "${f}.bak" ]; then
    cp "$f" "${f}.bak"
    echo "  backup: ${f}.bak"
  fi
done

echo "=== Protect local learned files ==="
for f in "${PROTECT[@]}"; do
  if git ls-files --error-unmatch "$f" &>/dev/null; then
    git update-index --skip-worktree "$f"
    echo "  skip-worktree: $f"
  fi
done

if [ ! -f prompts.json ] && [ -f prompts.json.example ]; then
  cp prompts.json.example prompts.json
  echo "  created prompts.json from example"
fi
if [ ! -f workflows/app-photo-video.json ] && [ -f workflows/app-photo-video.json.example ]; then
  mkdir -p workflows
  cp workflows/app-photo-video.json.example workflows/app-photo-video.json
  echo "  created workflow from example"
fi

echo "=== git pull (code only) ==="
git pull

echo "Done. Learned data unchanged:"
ls -la ratings.sqlite prompts.json workflows/app-photo-video.json 2>/dev/null || true
