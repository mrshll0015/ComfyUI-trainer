#!/bin/bash
# RunPod: clone into /workspace/runpod-slim/trainer && bash start-trainer.sh
set -e

ROOT="${RUNPOD_ROOT:-/workspace/runpod-slim}"
LOG="$ROOT/trainer.log"
PY="${PY:-python3}"
PORT="${TRAINER_PORT:-8189}"
WORKFLOW="${TRAINER_WORKFLOW:-app-photo-video.json}"

cd "$ROOT"

pkill -f "trainer.web" 2>/dev/null || true
sleep 1

export COMFY_HOST="${COMFY_HOST:-127.0.0.1}"
export COMFY_PORT="${COMFY_PORT:-8188}"
export TRAINER_WORKFLOW="$WORKFLOW"
export TRAINER_PORT="$PORT"
export RUNPOD_ROOT="$ROOT"

: > "$LOG"
nohup "$PY" -m trainer.web --host 0.0.0.0 --port "$PORT" --workflow "$WORKFLOW" >> "$LOG" 2>&1 &
PID=$!

echo "Trainer PID $PID — waiting for :$PORT (up to 30s)..."
for i in $(seq 1 15); do
  if ! kill -0 "$PID" 2>/dev/null; then
    echo "Trainer died. Log:"
    tail -n 30 "$LOG"
    exit 1
  fi
  if curl -sf "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
    echo "OK — Trainer UI on port $PORT"
    echo "  https://YOUR-POD-ID-${PORT}.proxy.runpod.net/"
    exit 0
  fi
  sleep 2
done

echo "Still starting. tail -f $LOG"
