#!/usr/bin/env bash
# Start the Pi 5 AI services and the live browser dashboard.
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="$ROOT/pi5_assistant"
PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
LOG_DIR="$ROOT/logs"
PIDS=()

if [[ ! -x "$PYTHON" ]]; then
  echo "Python environment not found: $PYTHON" >&2
  echo "Create it first with: python3 -m venv .venv && .venv/bin/pip install -r pi5_assistant/requirements.txt" >&2
  exit 1
fi

if [[ -f "$ROOT/.env.local" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env.local"
  set +a
fi

mkdir -p "$LOG_DIR"
cd "$APP"

start_service() {
  local name="$1"; shift
  echo "Starting $name…"
  "$@" >"$LOG_DIR/$name.log" 2>&1 &
  PIDS+=("$!")
}

cleanup() {
  echo
  echo "Stopping services…"
  kill "${PIDS[@]}" 2>/dev/null || true
  wait "${PIDS[@]}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

if ! pgrep -x mosquitto >/dev/null; then
  echo "Mosquitto is not running. Start it with: sudo systemctl start mosquitto" >&2
  exit 1
fi

if ss -ltn '( sport = :8000 )' 2>/dev/null | grep -q ':8000'; then
  echo "Using existing Hailo/Ollama proxy on port 8000."
else
  start_service hailo_proxy "$PYTHON" -m llm_orchestrator.hailo_ollama_proxy
fi
start_service vision "$PYTHON" -m vision_service.main
start_service voice "$PYTHON" -m voice_service.main
start_service gpio "$PYTHON" -m gpio_service.main
start_service session "$PYTHON" -m session_manager.main
start_service llm "$PYTHON" -m llm_orchestrator.main
start_service dashboard "$PYTHON" -m dashboard_service.main

HOSTNAME="$(hostname -f 2>/dev/null || hostname)"
echo ""
echo "Dashboard: http://${HOSTNAME}:8080"
echo "For Tailscale, open: http://<your-pi-tailscale-name>:8080"
echo "Logs: $LOG_DIR (Ctrl-C stops all services)"
wait
