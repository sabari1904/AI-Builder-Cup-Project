#!/usr/bin/env bash
# Local dev server control: tools/dev.sh start|stop|restart|log
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PIDF="$ROOT/.dev.pid"; LOG="$ROOT/.dev.log"; PORT="${PORT:-8765}"

stop() { if [ -f "$PIDF" ]; then kill "$(cat "$PIDF")" 2>/dev/null || true; rm -f "$PIDF"; sleep 1; fi; }
start() {
  cd "$ROOT/backend"
  set -a; [ -f "$ROOT/.env" ] && . "$ROOT/.env"; set +a
  nohup "$ROOT/.venv/bin/python" -m uvicorn riposte.main:app --host 0.0.0.0 --port "$PORT" > "$LOG" 2>&1 &
  echo $! > "$PIDF"
  for _ in $(seq 1 20); do curl -fs "localhost:$PORT/healthz" >/dev/null 2>&1 && { echo "up on :$PORT"; return; }; sleep 0.5; done
  echo "failed to start"; tail -20 "$LOG"; exit 1
}
case "${1:-start}" in
  start) start ;; stop) stop ;; restart) stop; start ;; log) tail -50 "$LOG" ;;
esac
