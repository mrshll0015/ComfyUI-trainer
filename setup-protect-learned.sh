#!/bin/bash
# One-time on server after clone: lock learned files so git never overwrites them.
set -e
cd "$(dirname "$0")"

for f in prompts.json workflows/app-photo-video.json; do
  if [ -f "$f" ] && git ls-files --error-unmatch "$f" &>/dev/null; then
    git update-index --skip-worktree "$f"
    echo "skip-worktree: $f"
  elif [ -f "$f" ]; then
    echo "already untracked: $f"
  else
    echo "missing (ok): $f"
  fi
done

echo "ratings.sqlite is in .gitignore — never tracked."
echo "Use: bash pull-code-only.sh"
