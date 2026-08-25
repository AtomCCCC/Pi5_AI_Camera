#!/usr/bin/env bash
# Raspberry Pi 5 AI Camera project self-test.
#
# Safe default: software tests plus non-actuating Pi device checks.
# It captures one temporary camera frame and probes I2C, but never writes GPIO/PWM.
# Optional modes:
#   --e2e    run isolated MQTT service tests without GPIO or Voice
#   --llm    add one real LLM request to --e2e (may use a paid cloud API)
#   --audio  record, play, and synthesize a short audio sample
#   --servo  move both servos by a small amount (requires confirmation)

set -uo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"
APP_ROOT="$PROJECT_ROOT/pi5_assistant"
TEST_ROOT="$PROJECT_ROOT/tests"
LOG_DIR="$PROJECT_ROOT/logs"
RUN_STAMP="$(date +%Y%m%d_%H%M%S)"
LOG_FILE="$LOG_DIR/pi5_selftest_${RUN_STAMP}.log"
TEMP_ROOT=""

RUN_HARDWARE=1
RUN_E2E=0
RUN_LLM=0
RUN_AUDIO=0
RUN_SERVO=0
ASSUME_YES=0

PASS_COUNT=0
FAIL_COUNT=0
WARN_COUNT=0
SKIP_COUNT=0
IS_PI5=0

PYTHON_BIN="${PYTHON_BIN:-$PROJECT_ROOT/.venv/bin/python}"
PROJECT_PYTHONPATH="$APP_ROOT${PYTHONPATH:+:$PYTHONPATH}"
MQTT_HOST="${MQTT_HOST:-127.0.0.1}"
MQTT_PORT="${MQTT_PORT:-1883}"
LAST_PID=""

declare -a OWNED_PIDS=()
declare -a SERVICE_PIDS=()

usage() {
  cat <<'EOF'
Usage: ./test_pi5_project.sh [options]

Default checks:
  - credentials accidentally stored in tracked project files (file names only)
  - Python/Bash/YAML syntax, imports, and all unittest tests
  - MQTT broker loopback
  - on a Pi 5: power state, Hailo-10H, models, one camera capture, PWM state,
    audio device listing, and an I2C probe (no GPIO/PWM writes)

Options:
  --software-only  Skip Pi hardware checks.
  --e2e            Run Control/Session/Dashboard on a private temporary MQTT
                   broker; add Vision when Pi camera/Hailo hardware is present.
                   GPIO and Voice services are deliberately not started.
  --llm            Include one real LLM request; implies --e2e. This can use
                   DeepSeek credit when router.prefer_online is true.
  --audio          Record/play three seconds and test Piper TTS.
  --servo          Move pan and tilt by 5 degrees around centre. Requires an
                   external regulated 5 V supply, common ground, and confirmation.
  --yes            Skip confirmations for explicitly requested audio/servo tests.
  -h, --help       Show this help.

Examples:
  ./test_pi5_project.sh
  ./test_pi5_project.sh --e2e
  ./test_pi5_project.sh --e2e --llm
  ./test_pi5_project.sh --audio
  ./test_pi5_project.sh --servo
EOF
}

while (($#)); do
  case "$1" in
    --software-only)
      RUN_HARDWARE=0
      ;;
    --e2e)
      RUN_E2E=1
      ;;
    --llm)
      RUN_LLM=1
      RUN_E2E=1
      ;;
    --audio)
      RUN_AUDIO=1
      ;;
    --servo)
      RUN_SERVO=1
      ;;
    --yes)
      ASSUME_YES=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

if ! TEMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/pi5-ai-camera-test.XXXXXX")"; then
  echo "Unable to create the self-test temporary directory." >&2
  exit 2
fi
if ! mkdir -p "$LOG_DIR" || ! touch "$LOG_FILE"; then
  echo "Unable to create the self-test log under $LOG_DIR." >&2
  rmdir "$TEMP_ROOT" 2>/dev/null || true
  exit 2
fi
exec > >(tee -a "$LOG_FILE") 2>&1

section() {
  printf '\n========== %s ==========\n' "$1"
}

pass() {
  PASS_COUNT=$((PASS_COUNT + 1))
  printf '[PASS] %s\n' "$1"
}

fail() {
  FAIL_COUNT=$((FAIL_COUNT + 1))
  printf '[FAIL] %s\n' "$1"
}

warn() {
  WARN_COUNT=$((WARN_COUNT + 1))
  printf '[WARN] %s\n' "$1"
}

skip() {
  SKIP_COUNT=$((SKIP_COUNT + 1))
  printf '[SKIP] %s\n' "$1"
}

have() {
  command -v "$1" >/dev/null 2>&1
}

tail_log() {
  local path="$1"
  local lines="${2:-40}"
  if [[ -s "$path" ]]; then
    echo "--- ${path#$PROJECT_ROOT/} (last $lines lines) ---"
    tail -n "$lines" "$path"
  fi
}

register_pid() {
  OWNED_PIDS+=("$1")
}

wait_owned_pid() {
  local pid="$1"
  local status=0
  local item
  local -a remaining=()
  wait "$pid" 2>/dev/null || status=$?
  for item in "${OWNED_PIDS[@]}"; do
    if [[ "$item" != "$pid" ]]; then
      remaining+=("$item")
    fi
  done
  OWNED_PIDS=("${remaining[@]}")
  return "$status"
}

stop_pid() {
  local pid="$1"
  local attempt
  if ! kill -0 "$pid" 2>/dev/null; then
    wait "$pid" 2>/dev/null || true
    return
  fi
  kill -INT "$pid" 2>/dev/null || true
  for attempt in {1..20}; do
    if ! kill -0 "$pid" 2>/dev/null; then
      wait "$pid" 2>/dev/null || true
      return
    fi
    sleep 0.1
  done
  kill -TERM "$pid" 2>/dev/null || true
  for attempt in {1..20}; do
    if ! kill -0 "$pid" 2>/dev/null; then
      wait "$pid" 2>/dev/null || true
      return
    fi
    sleep 0.1
  done
  kill -KILL "$pid" 2>/dev/null || true
  wait "$pid" 2>/dev/null || true
}

stop_services() {
  local index
  for ((index=${#SERVICE_PIDS[@]} - 1; index >= 0; index--)); do
    stop_pid "${SERVICE_PIDS[$index]}"
  done
  SERVICE_PIDS=()
}

cleanup() {
  local index
  stop_services
  for ((index=${#OWNED_PIDS[@]} - 1; index >= 0; index--)); do
    if kill -0 "${OWNED_PIDS[$index]}" 2>/dev/null; then
      stop_pid "${OWNED_PIDS[$index]}"
    fi
  done
  if [[ -n "$TEMP_ROOT" && -d "$TEMP_ROOT" && \
        "$TEMP_ROOT" == "${TMPDIR:-/tmp}"/pi5-ai-camera-test.* ]]; then
    rm -rf -- "$TEMP_ROOT"
  fi
}

trap cleanup EXIT
trap 'exit 130' INT TERM

confirm_action() {
  local prompt="$1"
  local answer
  if ((ASSUME_YES)); then
    return 0
  fi
  if [[ ! -t 0 ]]; then
    fail "$prompt (non-interactive terminal; pass --yes only after checking safety)"
    return 1
  fi
  read -r -p "$prompt Type YES to continue: " answer
  [[ "$answer" == "YES" ]]
}

check_import() {
  local label="$1"
  local module="$2"
  local required="${3:-1}"
  local output="$TEMP_ROOT/import_${module//[^A-Za-z0-9]/_}.log"
  if env PYTHONPATH="$PROJECT_PYTHONPATH" "$PYTHON_BIN" - "$module" \
      >"$output" 2>&1 <<'PY'
import importlib
import sys

importlib.import_module(sys.argv[1])
PY
  then
    pass "$label"
  elif ((required)); then
    fail "$label"
    tail_log "$output" 8
  else
    warn "$label (optional or configuration-dependent)"
  fi
}

json_file_valid() {
  local path="$1"
  env PYTHONPATH="$PROJECT_PYTHONPATH" "$PYTHON_BIN" - "$path" \
      >/dev/null 2>&1 <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    json.load(stream)
PY
}

json_field_nonempty() {
  local path="$1"
  local field="$2"
  env PYTHONPATH="$PROJECT_PYTHONPATH" "$PYTHON_BIN" - "$path" "$field" \
      >/dev/null 2>&1 <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    value = json.load(stream)
for key in sys.argv[2].split("."):
    value = value[key]
assert value not in (None, "", [], {}), value
PY
}

subscribe_once_background() {
  local topic="$1"
  local output="$2"
  local seconds="${3:-10}"
  timeout "$seconds" mosquitto_sub \
    -h "$MQTT_HOST" -p "$MQTT_PORT" \
    -C 1 -t "$topic" >"$output" 2>"${output}.err" &
  LAST_PID=$!
  register_pid "$LAST_PID"
}

subscribe_matching_background() {
  local topic="$1"
  local output="$2"
  local seconds="$3"
  local field="$4"
  local expected="$5"
  env PYTHONPATH="$PROJECT_PYTHONPATH" "$PYTHON_BIN" - \
      "$MQTT_HOST" "$MQTT_PORT" "$topic" "$output" "$seconds" \
      "$field" "$expected" >"${output}.err" 2>&1 <<'PY' &
import json
import os
from pathlib import Path
import sys
import threading
import time

import paho.mqtt.client as mqtt

host, port, topic, output, timeout_s, field, expected = sys.argv[1:]
matched = {}
ready = threading.Event()
done = threading.Event()


def nested(payload, dotted):
    value = payload
    for key in dotted.split("."):
        value = value[key]
    return value


def on_connect(client, _userdata, _flags, reason_code, _properties=None):
    if reason_code == 0:
        client.subscribe(topic)
        ready.set()


def on_message(_client, _userdata, message):
    try:
        payload = json.loads(message.payload.decode("utf-8"))
        if str(nested(payload, field)) != expected:
            return
    except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
        return
    matched["payload"] = payload
    Path(output).write_text(json.dumps(payload), encoding="utf-8")
    done.set()


client = mqtt.Client(client_id=f"t{os.getpid()}{time.time_ns() & 0xffff:x}")
client.on_connect = on_connect
client.on_message = on_message
try:
    client.connect(host, int(port), keepalive=20)
    client.loop_start()
    if not ready.wait(min(3.0, float(timeout_s))):
        raise TimeoutError("MQTT subscriber did not connect")
    if not done.wait(float(timeout_s)):
        raise TimeoutError(f"no matching message on {topic}")
finally:
    client.disconnect()
    client.loop_stop()
PY
  LAST_PID=$!
  register_pid "$LAST_PID"
}

publish_json() {
  local topic="$1"
  local payload="$2"
  mosquitto_pub \
    -h "$MQTT_HOST" -p "$MQTT_PORT" \
    -t "$topic" -m "$payload"
}

start_service() {
  local name="$1"
  local module="$2"
  local destination="$3"
  [[ -n "$name" ]] || return 2
  (
    cd "$APP_ROOT" || exit 1
    exec env PYTHONPATH="$PROJECT_PYTHONPATH" \
      "$PYTHON_BIN" -m "$module"
  ) >"$destination" 2>&1 &
  LAST_PID=$!
  SERVICE_PIDS+=("$LAST_PID")
}

start_configured_service() {
  local name="$1"
  local module="$2"
  local class_name="$3"
  local config_path="$4"
  local destination="$5"
  [[ -n "$name" ]] || return 2
  (
    cd "$APP_ROOT" || exit 1
    exec env PYTHONPATH="$PROJECT_PYTHONPATH" \
      "$PYTHON_BIN" -c \
      'import importlib, sys; getattr(importlib.import_module(sys.argv[1]), sys.argv[2])(config_path=sys.argv[3]).run()' \
      "$module" "$class_name" "$config_path"
  ) >"$destination" 2>&1 &
  LAST_PID=$!
  SERVICE_PIDS+=("$LAST_PID")
}

start_dashboard_service() {
  local destination="$1"
  local dashboard_port="$2"
  (
    cd "$APP_ROOT" || exit 1
    exec env PYTHONPATH="$PROJECT_PYTHONPATH" \
      MQTT_BROKER="$MQTT_HOST" MQTT_PORT="$MQTT_PORT" \
      DASHBOARD_PORT="$dashboard_port" \
      "$PYTHON_BIN" -c \
      'import os; from dashboard_service.main import create_app; create_app().run(host="127.0.0.1", port=int(os.environ["DASHBOARD_PORT"]), threaded=True, use_reloader=False)'
  ) >"$destination" 2>&1 &
  LAST_PID=$!
  SERVICE_PIDS+=("$LAST_PID")
}

start_private_broker() {
  local config_path="$1"
  local destination="$2"
  mosquitto -c "$config_path" >"$destination" 2>&1 &
  LAST_PID=$!
  SERVICE_PIDS+=("$LAST_PID")
}

find_free_port() {
  "$PYTHON_BIN" - <<'PY'
import socket

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
}

write_isolated_config() {
  local source_path="$1"
  local destination="$2"
  "$PYTHON_BIN" - "$source_path" "$destination" "$MQTT_HOST" "$MQTT_PORT" <<'PY'
from pathlib import Path
import sys

import yaml

source, destination, host, port = sys.argv[1:]
with open(source, encoding="utf-8") as stream:
    config = yaml.safe_load(stream)
if not isinstance(config, dict) or not isinstance(config.get("mqtt"), dict):
    raise ValueError(f"missing mqtt mapping in {source}")
config["mqtt"]["broker"] = host
config["mqtt"]["port"] = int(port)
Path(destination).write_text(
    yaml.safe_dump(config, sort_keys=False, allow_unicode=True),
    encoding="utf-8",
)
PY
}

read_yaml_value() {
  local file="$1"
  shift
  env PYTHONPATH="$PROJECT_PYTHONPATH" "$PYTHON_BIN" - "$file" "$@" \
      2>/dev/null <<'PY'
import sys
import yaml

with open(sys.argv[1], encoding="utf-8") as stream:
    value = yaml.safe_load(stream)
for key in sys.argv[2:]:
    value = value[key]
if isinstance(value, bool):
    print("true" if value else "false")
else:
    print(value)
PY
}

echo "Pi 5 AI Camera self-test"
echo "Project: $PROJECT_ROOT"
echo "Log:     $LOG_FILE"
echo "Time:    $(date --iso-8601=seconds 2>/dev/null || date)"

if ((EUID == 0)); then
  warn "The complete script is running as root; normal-user execution is preferred"
fi

section "Project layout and security"

for required_path in \
  "$APP_ROOT/run_all.sh" \
  "$APP_ROOT/requirements.txt" \
  "$APP_ROOT/vision_service/config.yaml" \
  "$APP_ROOT/control_service/config.yaml" \
  "$APP_ROOT/gpio_service/config.yaml" \
  "$TEST_ROOT"; do
  if [[ -e "$required_path" ]]; then
    pass "Found ${required_path#$PROJECT_ROOT/}"
  else
    fail "Missing ${required_path#$PROJECT_ROOT/}"
  fi
done

SECRET_FILES="$TEMP_ROOT/secret_files.txt"
grep -rIlE \
  --exclude-dir=.git \
  --exclude-dir=.venv \
  --exclude-dir=venv \
  --exclude-dir=__pycache__ \
  --exclude-dir=logs \
  --exclude='.env*' \
  '(sk-[A-Za-z0-9_-]{20,}|github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|-----BEGIN (RSA |OPENSSH |EC )?PRIVATE KEY-----)' \
  "$PROJECT_ROOT" >"$SECRET_FILES" 2>/dev/null || true

if [[ -s "$SECRET_FILES" ]]; then
  fail "Possible plaintext credentials found; rotate them and remove them from Git history"
  while IFS= read -r secret_file; do
    echo "       ${secret_file#$PROJECT_ROOT/}"
  done <"$SECRET_FILES"
else
  pass "No obvious plaintext API tokens/private keys in project files"
fi

section "Python environment and dependencies"

if [[ -x "$PYTHON_BIN" ]]; then
  pass "Project Python found: $PYTHON_BIN"
else
  fail "Project Python is missing: $PYTHON_BIN"
  if have python3; then
    PYTHON_BIN="$(command -v python3)"
    warn "Using system Python for remaining checks: $PYTHON_BIN"
  else
    echo "No Python interpreter is available; cannot continue." >&2
    exit 1
  fi
fi

"$PYTHON_BIN" --version
PATH="$(dirname "$PYTHON_BIN"):$PATH"
export PATH

check_import "PyYAML import" yaml
check_import "Paho MQTT import" paho.mqtt.client
check_import "Flask import" flask
check_import "NumPy import" numpy
check_import "OpenCV import" cv2
check_import "Requests import" requests
check_import "OpenAI SDK import" openai
check_import "SoundDevice import" sounddevice
check_import "OpenWakeWord import" openwakeword
check_import "Faster Whisper import" faster_whisper
check_import "GPIO Zero import" gpiozero
check_import "lgpio import" lgpio
check_import "Ollama Python client import" ollama 0
check_import "Adafruit SSD1306 import" adafruit_ssd1306 0
check_import "Adafruit board import" board 0
check_import "Adafruit busio import" busio 0

if have piper; then
  pass "Piper CLI found"
else
  fail "Piper CLI missing (voice_service requires it for TTS)"
fi

section "Static checks and unit tests"

if bash -n "$APP_ROOT/run_all.sh"; then
  pass "run_all.sh Bash syntax"
else
  fail "run_all.sh Bash syntax"
fi

if bash -n "$PROJECT_ROOT/test_pi5_project.sh"; then
  pass "test_pi5_project.sh Bash syntax"
else
  fail "test_pi5_project.sh Bash syntax"
fi

STATIC_LOG="$TEMP_ROOT/static_python.log"
if env PYTHONPATH="$PROJECT_PYTHONPATH" "$PYTHON_BIN" - "$PROJECT_ROOT" \
    >"$STATIC_LOG" 2>&1 <<'PY'
import ast
from pathlib import Path
import sys
import yaml

root = Path(sys.argv[1])
python_files = [
    path for path in root.rglob("*.py")
    if not {".venv", "venv", "__pycache__"}.intersection(path.parts)
]
for path in python_files:
    ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))

yaml_files = list((root / "pi5_assistant").rglob("*.yaml"))
for path in yaml_files:
    yaml.safe_load(path.read_text(encoding="utf-8-sig"))

print(f"{len(python_files)} Python files and {len(yaml_files)} YAML files parsed")
PY
then
  pass "Python and YAML files parse successfully ($(cat "$STATIC_LOG"))"
else
  fail "Python or YAML parse check"
  tail_log "$STATIC_LOG" 30
fi

UNIT_LOG="$TEMP_ROOT/unittest.log"
if (
  cd "$PROJECT_ROOT" || exit 1
  env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PROJECT_PYTHONPATH" \
    "$PYTHON_BIN" -B -m unittest discover \
      -s tests -p 'test_*.py' -v
) >"$UNIT_LOG" 2>&1; then
  TEST_TOTAL="$(grep -Eo 'Ran [0-9]+ tests?' "$UNIT_LOG" | tail -n 1 || true)"
  pass "Hardware-independent unit tests (${TEST_TOTAL:-completed})"
else
  fail "Hardware-independent unit tests"
  tail_log "$UNIT_LOG" 80
fi

if have shellcheck; then
  SHELLCHECK_LOG="$TEMP_ROOT/shellcheck.log"
  if shellcheck -x "$PROJECT_ROOT/test_pi5_project.sh" "$APP_ROOT/run_all.sh" \
      >"$SHELLCHECK_LOG" 2>&1; then
    pass "ShellCheck"
  else
    fail "ShellCheck"
    tail_log "$SHELLCHECK_LOG" 80
  fi
else
  skip "ShellCheck is not installed (optional: sudo apt install shellcheck)"
fi

section "MQTT broker loopback"

if have timeout && have mosquitto_pub && have mosquitto_sub; then
  MQTT_TOPIC="pi5/selftest/$$/$RUN_STAMP"
  MQTT_OUTPUT="$TEMP_ROOT/mqtt_loopback.txt"
  subscribe_once_background "$MQTT_TOPIC" "$MQTT_OUTPUT" 6
  MQTT_PID="$LAST_PID"
  sleep 0.4
  if publish_json "$MQTT_TOPIC" 'PI5_MQTT_OK' && wait_owned_pid "$MQTT_PID"; then
    if grep -qxF 'PI5_MQTT_OK' "$MQTT_OUTPUT"; then
      pass "Mosquitto publish/subscribe loopback"
    else
      fail "Mosquitto loopback returned unexpected data"
    fi
  else
    fail "Mosquitto broker is unavailable at $MQTT_HOST:$MQTT_PORT"
    tail_log "${MQTT_OUTPUT}.err" 20
    echo "       Start it with: sudo systemctl start mosquitto"
  fi
else
  fail "timeout/mosquitto_pub/mosquitto_sub commands are required"
fi

if ((RUN_HARDWARE)); then
  section "Raspberry Pi 5 platform and power"

  DEVICE_MODEL=""
  if [[ -r /proc/device-tree/model ]]; then
    DEVICE_MODEL="$(tr -d '\0' </proc/device-tree/model)"
  fi
  ARCHITECTURE="$(uname -m 2>/dev/null || true)"
  echo "Model: ${DEVICE_MODEL:-unknown}"
  echo "Arch:  ${ARCHITECTURE:-unknown}"

  if [[ "$DEVICE_MODEL" == *"Raspberry Pi 5"* ]]; then
    pass "Raspberry Pi 5 detected"
    IS_PI5=1
  else
    warn "Not running on a Raspberry Pi 5; Pi-specific checks will be skipped"
  fi

  if ((IS_PI5)); then
    if [[ "$ARCHITECTURE" == "aarch64" ]]; then
      pass "64-bit aarch64 operating system"
    else
      fail "Expected aarch64, got ${ARCHITECTURE:-unknown}"
    fi

    if have vcgencmd; then
      THROTTLED="$(vcgencmd get_throttled 2>&1 || true)"
      TEMPERATURE="$(vcgencmd measure_temp 2>&1 || true)"
      echo "$TEMPERATURE"
      if [[ "$THROTTLED" == "throttled=0x0" ]]; then
        pass "No current or historical under-voltage/throttling flags"
      else
        fail "Power/throttling state: $THROTTLED"
      fi
    else
      fail "vcgencmd is unavailable"
    fi

    section "Hailo-10H"

    check_import "HailoRT Python binding import" hailo_platform
    check_import "Hailo Apps import" hailo_apps

    if have hailortcli; then
      HAILO_IDENTIFY="$TEMP_ROOT/hailo_identify.txt"
      if timeout 20 hailortcli fw-control identify >"$HAILO_IDENTIFY" 2>&1; then
        if grep -q 'HAILO10H' "$HAILO_IDENTIFY"; then
          pass "Hailo-10H firmware/device identification"
        else
          fail "Hailo device found, but architecture is not HAILO10H"
          tail_log "$HAILO_IDENTIFY" 30
        fi
      else
        fail "hailortcli could not identify the Hailo device"
        tail_log "$HAILO_IDENTIFY" 30
      fi
    else
      fail "hailortcli is missing"
    fi

    YOLO_HEF="/usr/local/hailo/resources/models/hailo10h/hailo_yolov8n_4_classes_vga.hef"
    LLM_HEF="/usr/local/hailo/resources/models/hailo10h/Qwen2.5-1.5B-Instruct.hef"
    if [[ -s "$YOLO_HEF" ]]; then
      pass "YOLO HEF present"
    else
      fail "YOLO HEF missing: $YOLO_HEF"
    fi
    if [[ -s "$LLM_HEF" ]]; then
      pass "Hailo LLM HEF present"
    else
      fail "Hailo LLM HEF missing: $LLM_HEF"
    fi

    VLM_HELP_LOG="$TEMP_ROOT/hailo_vlm_help.log"
    if timeout 30 env PYTHONPATH="$PROJECT_PYTHONPATH" "$PYTHON_BIN" \
      -m hailo_apps.python.gen_ai_apps.vlm_chat.vlm_chat --help \
      >"$VLM_HELP_LOG" 2>&1; then
      pass "Configured Hailo VLM Python module is callable"
    else
      fail "Configured Hailo VLM module/CLI is incompatible or unavailable"
      tail_log "$VLM_HELP_LOG" 30
    fi

    section "Camera Module"

    if have rpicam-hello && have rpicam-jpeg; then
      CAMERA_LIST="$TEMP_ROOT/camera_list.txt"
      if timeout 15 rpicam-hello --list-cameras >"$CAMERA_LIST" 2>&1; then
        if grep -qi 'imx708' "$CAMERA_LIST"; then
          pass "Camera Module 3 / IMX708 detected"
        else
          fail "Camera list does not contain IMX708"
          tail_log "$CAMERA_LIST" 40
        fi
      else
        fail "Unable to list cameras"
        tail_log "$CAMERA_LIST" 40
      fi

      CAMERA_JPEG="$TEMP_ROOT/pi5_camera_640.jpg"
      CAMERA_CAPTURE_LOG="$TEMP_ROOT/camera_capture.log"
      if timeout 15 rpicam-jpeg \
          --nopreview --timeout 1000 \
          --width 640 --height 640 \
          --output "$CAMERA_JPEG" \
          >"$CAMERA_CAPTURE_LOG" 2>&1 && [[ -s "$CAMERA_JPEG" ]]; then
        if env PYTHONPATH="$PROJECT_PYTHONPATH" "$PYTHON_BIN" - "$CAMERA_JPEG" \
            >/dev/null 2>&1 <<'PY'
import cv2
import sys

image = cv2.imread(sys.argv[1])
assert image is not None
assert image.shape[:2] == (640, 640), image.shape
PY
        then
          pass "640x640 camera capture and OpenCV decode"
        else
          fail "Camera JPEG does not decode as 640x640"
        fi
      else
        fail "Camera capture failed or returned an empty JPEG"
        tail_log "$CAMERA_CAPTURE_LOG" 40
      fi
    else
      fail "rpicam-hello/rpicam-jpeg commands are missing"
    fi

    section "PWM, OLED, and audio devices"

    PWM_CONFIG="$APP_ROOT/gpio_service/config.yaml"
    PWM_CHIP="$(read_yaml_value "$PWM_CONFIG" pwm chip || true)"
    echo "Configured PWM chip: ${PWM_CHIP:-unknown}"

    PWM_OVERLAY_LINES="$TEMP_ROOT/pwm_overlay_lines.txt"
    : >"$PWM_OVERLAY_LINES"
    for boot_config in /boot/firmware/config.txt /boot/config.txt; do
      if [[ -r "$boot_config" ]]; then
        grep -E '^[[:space:]]*dtoverlay[[:space:]]*=[[:space:]]*pwm-2chan([,[:space:]]|$)' \
          "$boot_config" 2>/dev/null | tr -d '[:space:]' >>"$PWM_OVERLAY_LINES" || true
      fi
    done
    if grep -Eq '(^|,)pin=12(,|$)' "$PWM_OVERLAY_LINES" && \
       grep -Eq '(^|,)pin2=13(,|$)' "$PWM_OVERLAY_LINES"; then
      pass "Dual-channel GPIO12/GPIO13 PWM overlay configured"
    else
      warn "Could not confirm the GPIO12/GPIO13 overlay text (runtime checks follow)"
    fi

    if have pinctrl; then
      PINCTRL_OUTPUT="$TEMP_ROOT/pinctrl.txt"
      if pinctrl get 12 >"$PINCTRL_OUTPUT" 2>&1 && \
          pinctrl get 13 >>"$PINCTRL_OUTPUT" 2>&1 && \
          [[ "$(grep -ci 'pwm' "$PINCTRL_OUTPUT" || true)" -ge 2 ]]; then
        pass "GPIO12/GPIO13 pin functions report PWM"
      else
        fail "GPIO12/GPIO13 are not reporting PWM functions"
        tail_log "$PINCTRL_OUTPUT" 20
      fi
    else
      fail "pinctrl command is unavailable"
    fi

    if [[ -n "$PWM_CHIP" && -d "$PWM_CHIP" ]]; then
      PWM_CHANNEL_COUNT="$(cat "$PWM_CHIP/npwm" 2>/dev/null || echo 0)"
      if [[ "$PWM_CHANNEL_COUNT" =~ ^[0-9]+$ ]] && ((PWM_CHANNEL_COUNT >= 2)); then
        pass "Configured PWM controller exists with at least two channels"
      else
        fail "Configured PWM controller exposes fewer than two channels"
      fi
      PWM_CHANNELS="$TEMP_ROOT/pwm_channels.txt"
      "$PYTHON_BIN" - "$PWM_CONFIG" >"$PWM_CHANNELS" <<'PY'
import sys
import yaml

with open(sys.argv[1], encoding="utf-8") as stream:
    config = yaml.safe_load(stream)
channels = sorted({int(value) for value in config["pwm"]["gpio_to_channel"].values()})
print("\n".join(map(str, channels)))
PY
      PWM_EXISTING_WRITABLE=1
      while IFS= read -r channel; do
        [[ "$channel" =~ ^[0-9]+$ ]] || continue
        for attribute in duty_cycle enable period; do
          if [[ ! -w "$PWM_CHIP/pwm$channel/$attribute" ]]; then
            PWM_EXISTING_WRITABLE=0
          fi
        done
      done <"$PWM_CHANNELS"
      if [[ -w "$PWM_CHIP/export" || "$PWM_EXISTING_WRITABLE" == "1" ]]; then
        pass "Current user can configure/export the mapped PWM channels"
      else
        fail "Current user cannot configure mapped PWM channels or write $PWM_CHIP/export"
      fi
    else
      fail "Configured PWM controller does not exist: ${PWM_CHIP:-unset}"
      echo "       Available controllers:"
      find /sys/class/pwm -maxdepth 1 -name 'pwmchip*' -print 2>/dev/null || true
    fi

    if [[ -e /dev/i2c-1 ]]; then
      pass "I2C bus /dev/i2c-1 exists"
      if have i2cdetect && [[ -r /dev/i2c-1 && -w /dev/i2c-1 ]]; then
        I2C_OUTPUT="$TEMP_ROOT/i2cdetect.txt"
        if timeout 10 i2cdetect -y 1 >"$I2C_OUTPUT" 2>&1 && \
            grep -Eq '(^|[[:space:]])3c([[:space:]]|$)' "$I2C_OUTPUT"; then
          pass "SSD1306 device detected at I2C address 0x3c"
        else
          warn "No SSD1306 response at 0x3c (screen is optional and falls back to dummy)"
        fi
      else
        warn "Cannot scan I2C as the current user"
      fi
    else
      warn "I2C bus /dev/i2c-1 is absent"
    fi

    if have arecord && LC_ALL=C arecord -l >"$TEMP_ROOT/arecord.txt" 2>&1 && \
        grep -q '^card ' "$TEMP_ROOT/arecord.txt"; then
      pass "ALSA capture device detected"
    else
      fail "No ALSA microphone/capture device detected"
    fi

    if have aplay && LC_ALL=C aplay -l >"$TEMP_ROOT/aplay.txt" 2>&1 && \
        grep -q '^card ' "$TEMP_ROOT/aplay.txt"; then
      pass "ALSA playback device detected"
    else
      fail "No ALSA speaker/playback device detected"
    fi

    SOUNDDEVICE_LOG="$TEMP_ROOT/sounddevice_devices.txt"
    if env PYTHONPATH="$PROJECT_PYTHONPATH" "$PYTHON_BIN" \
        >"$SOUNDDEVICE_LOG" 2>&1 <<'PY'
import sounddevice as sd

devices = sd.query_devices()
input_index, output_index = sd.default.device
assert devices, "no PortAudio devices"
assert input_index is not None and int(input_index) >= 0, sd.default.device
assert output_index is not None and int(output_index) >= 0, sd.default.device
print(devices)
print("default:", sd.default.device)
PY
    then
      pass "SoundDevice has default input and output devices"
    else
      fail "SoundDevice default input/output is not ready"
      tail_log "$SOUNDDEVICE_LOG" 30
      echo "       Note: voice_service/config.yaml mic_device is not used by the current code."
    fi
  fi
else
  skip "Pi hardware checks disabled by --software-only"
fi

run_audio_test() {
  section "Active audio test"
  if ((IS_PI5 == 0)); then
    skip "Active audio test requires Raspberry Pi hardware"
    return
  fi
  if ! confirm_action "This will record and play audio."; then
    skip "Active audio test cancelled"
    return
  fi
  if ! have arecord || ! have aplay || ! have piper; then
    fail "arecord, aplay, and piper are required for the active audio test"
    return
  fi

  local voice_config="$APP_ROOT/voice_service/config.yaml"
  local mic_device
  local piper_voice
  local mic_wave="$TEMP_ROOT/microphone_test.wav"
  local tts_wave="$TEMP_ROOT/piper_test.wav"
  mic_device="$(read_yaml_value "$voice_config" mic_device || true)"
  piper_voice="$(read_yaml_value "$voice_config" tts voice || true)"

  if timeout 10 arecord \
      -D "${mic_device:-default}" \
      -f S16_LE -r 16000 -c 1 -d 3 "$mic_wave" \
      >"$TEMP_ROOT/active_arecord.log" 2>&1 && [[ -s "$mic_wave" ]]; then
    pass "Three-second microphone recording"
  else
    fail "Microphone recording using ${mic_device:-default}"
    tail_log "$TEMP_ROOT/active_arecord.log" 30
    return
  fi

  if timeout 10 aplay "$mic_wave" >"$TEMP_ROOT/active_aplay.log" 2>&1; then
    pass "Microphone recording playback"
  else
    fail "Microphone recording playback"
    tail_log "$TEMP_ROOT/active_aplay.log" 30
  fi

  if printf '%s\n' 'Raspberry Pi five audio self test.' | \
      timeout 90 piper \
        --model "${piper_voice:-en_US-amy-medium}" \
        --output_file "$tts_wave" \
        >"$TEMP_ROOT/piper.log" 2>&1 && [[ -s "$tts_wave" ]]; then
    pass "Piper speech synthesis"
    if timeout 15 aplay "$tts_wave" >"$TEMP_ROOT/piper_aplay.log" 2>&1; then
      pass "Piper speech playback"
    else
      fail "Piper speech playback"
      tail_log "$TEMP_ROOT/piper_aplay.log" 30
    fi
  else
    fail "Piper model/voice is not usable: ${piper_voice:-en_US-amy-medium}"
    tail_log "$TEMP_ROOT/piper.log" 40
  fi
}

wait_for_http() {
  local url="$1"
  local seconds="$2"
  local destination="$3"
  local attempt
  for ((attempt=0; attempt<seconds; attempt++)); do
    if curl -fsS --max-time 2 "$url" -o "$destination" 2>/dev/null; then
      return 0
    fi
    sleep 1
  done
  return 1
}

validate_session_forward() {
  "$PYTHON_BIN" - "$1" "$2" "$3" >/dev/null 2>&1 <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    payload = json.load(stream)
text, session_id = sys.argv[2:]
assert payload["text"] == text
assert payload["session_id"] == session_id
history = payload["history"]
assert isinstance(history, list)
assert {"role": "user", "content": text} in history
PY
}

validate_control_outputs() {
  "$PYTHON_BIN" - "$1" "$2" "$3" >/dev/null 2>&1 <<'PY'
import json
import math
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    command = json.load(stream)
with open(sys.argv[2], encoding="utf-8") as stream:
    status = json.load(stream)
session_id = sys.argv[3]

assert command["type"] == "gimbal"
assert command["source"] == "visual_pid"
assert command["session_id"] == session_id
angles = command["angles"]
assert set(angles) == {"1", "2"}
assert all(isinstance(value, (int, float)) and not isinstance(value, bool)
           for value in angles.values())
assert all(20 <= value <= 160 for value in angles.values())
assert angles["1"] > 90 and abs(angles["2"] - 90) <= 1

assert status["session_id"] == session_id
assert status["mode"] == "tracking" and status["pid_active"] is True
assert status["roi_center"] == [540.0, 320.0]
assert status["frame_center"] == [320.0, 320.0]
latency = float(status["latency_ms"])
assert math.isfinite(latency) and latency >= 0
PY
}

validate_dashboard_state() {
  "$PYTHON_BIN" - "$1" "$2" "$3" "$4" >/dev/null 2>&1 <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    state = json.load(stream)
detection_marker, vlm_marker, llm_marker = sys.argv[2:]
assert any(item.get("name") == detection_marker for item in state["detections"])
assert state["vlm"] == vlm_marker
assert state["llm"] == llm_marker
assert state["roi_generation"] >= 1
assert state["rois"] == []
assert state["roi_frame_size"] == {"width": 640, "height": 640}
assert isinstance(state["roi_instance_id"], str) and state["roi_instance_id"]
assert float(state["updated_at"]) > 0
PY
}

validate_vision_payload() {
  "$PYTHON_BIN" - "$1" >/dev/null 2>&1 <<'PY'
import json
import math
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    payload = json.load(stream)
assert isinstance(payload["detections"], list)
assert isinstance(payload.get("frame_size"), dict)
assert int(payload["frame_size"]["width"]) > 0
assert int(payload["frame_size"]["height"]) > 0
assert payload.get("coordinate_space") == "pixels"
assert math.isfinite(float(payload["timestamp"]))
PY
}

run_e2e_test() {
  section "Isolated non-actuating service E2E"

  for command_name in mosquitto mosquitto_pub mosquitto_sub timeout curl; do
    if ! have "$command_name"; then
      fail "E2E requires command: $command_name"
      return
    fi
  done

  local e2e_dir="$LOG_DIR/pi5_selftest_e2e_$RUN_STAMP"
  local private_port dashboard_port broker_pid control_pid session_pid dashboard_pid
  local service_ok=1
  local marker="selftest-$$-$RUN_STAMP"
  mkdir -p "$e2e_dir"

  private_port="$(find_free_port)" || private_port=""
  dashboard_port="$(find_free_port)" || dashboard_port=""
  while [[ -n "$private_port" && "$dashboard_port" == "$private_port" ]]; do
    dashboard_port="$(find_free_port)" || dashboard_port=""
  done
  if [[ ! "$private_port" =~ ^[0-9]+$ || ! "$dashboard_port" =~ ^[0-9]+$ ]]; then
    fail "Could not allocate private E2E ports"
    return
  fi

  # These locals dynamically scope every MQTT helper invoked by this function.
  # The listener is loopback-only, so no production GPIO subscriber can receive
  # the synthetic gimbal command generated below.
  local MQTT_HOST="127.0.0.1"
  local MQTT_PORT="$private_port"
  local broker_config="$TEMP_ROOT/e2e_mosquitto.conf"
  printf 'listener %s 127.0.0.1\nallow_anonymous true\npersistence false\n' \
    "$MQTT_PORT" >"$broker_config"
  start_private_broker "$broker_config" "$e2e_dir/mosquitto.log"
  broker_pid="$LAST_PID"

  local broker_ready=0
  local attempt
  for attempt in {1..40}; do
    if mosquitto_pub -h "$MQTT_HOST" -p "$MQTT_PORT" \
        -t "pi5/selftest/$marker/ready" -m ready >/dev/null 2>&1; then
      broker_ready=1
      break
    fi
    if ! kill -0 "$broker_pid" 2>/dev/null; then
      break
    fi
    sleep 0.1
  done
  if ((broker_ready == 0)); then
    fail "Private loopback-only Mosquitto broker did not start"
    tail_log "$e2e_dir/mosquitto.log" 60
    stop_services
    return
  fi
  pass "Private loopback-only MQTT broker ($MQTT_HOST:$MQTT_PORT)"

  local control_config="$TEMP_ROOT/control_e2e.yaml"
  local session_config="$TEMP_ROOT/session_e2e.yaml"
  local llm_config="$TEMP_ROOT/llm_e2e.yaml"
  if ! write_isolated_config "$APP_ROOT/control_service/config.yaml" "$control_config" || \
     ! write_isolated_config "$APP_ROOT/session_manager/config.yaml" "$session_config"; then
    fail "Could not create temporary isolated service configurations"
    stop_services
    return
  fi

  start_configured_service control control_service.main ControlService \
    "$control_config" "$e2e_dir/control.log"
  control_pid="$LAST_PID"
  start_configured_service session session_manager.main SessionManager \
    "$session_config" "$e2e_dir/session.log"
  session_pid="$LAST_PID"
  start_dashboard_service "$e2e_dir/dashboard.log" "$dashboard_port"
  dashboard_pid="$LAST_PID"

  sleep 2
  for service_spec in \
    "control:$control_pid" \
    "session:$session_pid" \
    "dashboard:$dashboard_pid"; do
    local service_name="${service_spec%%:*}"
    local service_pid="${service_spec##*:}"
    if kill -0 "$service_pid" 2>/dev/null; then
      pass "Isolated service stayed running: $service_name"
    else
      fail "Isolated service exited early: $service_name"
      tail_log "$e2e_dir/$service_name.log" 60
      service_ok=0
    fi
  done
  if ((service_ok == 0)); then
    stop_services
    return
  fi

  local session_text="PI5_SESSION_$marker"
  local command_payload="$TEMP_ROOT/e2e_command_in.json"
  local command_pid
  subscribe_matching_background command/in "$command_payload" 10 session_id "$marker"
  command_pid="$LAST_PID"
  sleep 0.7
  publish_json voice/transcript \
    "{\"text\":\"$session_text\",\"session_id\":\"$marker\"}" || true
  if wait_owned_pid "$command_pid" && \
      validate_session_forward "$command_payload" "$session_text" "$marker"; then
    pass "Session Manager preserved transcript, session ID, and history"
  else
    fail "Session Manager did not produce the expected command/in payload"
    tail_log "${command_payload}.err" 30
    tail_log "$e2e_dir/session.log" 50
  fi

  local control_command="$TEMP_ROOT/e2e_control_command.json"
  local control_status="$TEMP_ROOT/e2e_control_status.json"
  local control_command_pid control_status_pid control_command_ok=0 control_status_ok=0
  subscribe_matching_background gpio/command "$control_command" 10 session_id "$marker"
  control_command_pid="$LAST_PID"
  subscribe_matching_background control/status "$control_status" 10 session_id "$marker"
  control_status_pid="$LAST_PID"
  sleep 0.7
  local current_time
  current_time="$("$PYTHON_BIN" -c 'import time; print(time.time())')"
  publish_json vision/detections \
    "{\"frame_size\":{\"width\":640,\"height\":640},\"coordinate_space\":\"pixels\",\"tracking_target\":{\"name\":\"person\",\"confidence\":0.95,\"bbox\":[500,260,80,120],\"roi_center\":[540,320]},\"detections\":[{\"name\":\"person\",\"confidence\":0.95,\"bbox\":[500,260,80,120]}],\"timestamp\":$current_time,\"session_id\":\"$marker\"}" || true
  if wait_owned_pid "$control_command_pid"; then control_command_ok=1; fi
  if wait_owned_pid "$control_status_pid"; then control_status_ok=1; fi
  if ((control_command_ok && control_status_ok)) && \
      validate_control_outputs "$control_command" "$control_status" "$marker"; then
    pass "Control PID produced validated paired gimbal output on the private broker"
  else
    fail "Control PID output/status did not match the injected target"
    tail_log "${control_command}.err" 20
    tail_log "${control_status}.err" 20
    tail_log "$e2e_dir/control.log" 60
  fi

  local dashboard_state="$TEMP_ROOT/e2e_dashboard_state.json"
  local dashboard_url="http://127.0.0.1:$dashboard_port"
  if wait_for_http "$dashboard_url/api/state" 15 "$dashboard_state"; then
    pass "Dashboard HTTP API became ready on private port $dashboard_port"
  else
    fail "Dashboard HTTP API did not become ready"
    tail_log "$e2e_dir/dashboard.log" 60
  fi

  local detection_marker="PI5_DET_$marker"
  local vlm_marker="PI5_VLM_$marker"
  local llm_marker="PI5_LLM_$marker"
  current_time="$("$PYTHON_BIN" -c 'import time; print(time.time())')"
  publish_json vision/detections \
    "{\"detections\":[{\"name\":\"$detection_marker\",\"confidence\":0.99}],\"timestamp\":$current_time}" || true
  publish_json vision/result \
    "{\"description\":\"$vlm_marker\",\"session_id\":\"$marker\"}" || true
  publish_json response/out \
    "{\"text\":\"$llm_marker\",\"session_id\":\"$marker\"}" || true
  publish_json vision/roi \
    "{\"timestamp\":$current_time,\"frame_size\":{\"width\":640,\"height\":640},\"rois\":[]}" || true

  local dashboard_ok=0
  for attempt in {1..20}; do
    if curl -fsS --max-time 2 "$dashboard_url/api/state" -o "$dashboard_state" \
        2>/dev/null && validate_dashboard_state "$dashboard_state" \
        "$detection_marker" "$vlm_marker" "$llm_marker"; then
      dashboard_ok=1
      break
    fi
    sleep 0.25
  done
  if ((dashboard_ok)); then
    pass "Dashboard consumed detection, VLM, LLM, and empty-ROI updates"
  else
    fail "Dashboard state did not contain all injected MQTT updates"
    tail_log "$e2e_dir/dashboard.log" 60
  fi
  local dashboard_page="$TEMP_ROOT/e2e_dashboard_page.html"
  if curl -fsS --max-time 3 "$dashboard_url/" -o "$dashboard_page" \
      2>/dev/null && grep -q 'Pi 5 AI Camera' "$dashboard_page"; then
    pass "Dashboard page renders"
  else
    fail "Dashboard page did not render expected content"
  fi
  local frame_status
  frame_status="$(curl -sS --max-time 3 -o /dev/null -w '%{http_code}' \
    "$dashboard_url/frame.jpg" 2>/dev/null || true)"
  if [[ "$frame_status" == "200" || "$frame_status" == "204" ]]; then
    pass "Dashboard frame endpoint returned valid status $frame_status"
  else
    fail "Dashboard frame endpoint returned unexpected status ${frame_status:-none}"
  fi

  if ((IS_PI5)); then
    if have pgrep && pgrep -f 'vision_service[.]main' >/dev/null 2>&1; then
      skip "Vision service E2E skipped because another Vision process owns the camera"
    else
      local vision_config="$TEMP_ROOT/vision_e2e.yaml"
      local vision_pid detection_payload detection_pid detect_response detect_pid
      if write_isolated_config "$APP_ROOT/vision_service/config.yaml" "$vision_config"; then
        start_configured_service vision vision_service.main VisionService \
          "$vision_config" "$e2e_dir/vision.log"
        vision_pid="$LAST_PID"
        sleep 3
        if kill -0 "$vision_pid" 2>/dev/null; then
          pass "Isolated Vision service stayed running"
          detection_payload="$TEMP_ROOT/e2e_vision_detections.json"
          subscribe_once_background vision/detections "$detection_payload" 30
          detection_pid="$LAST_PID"
          if wait_owned_pid "$detection_pid" && validate_vision_payload "$detection_payload"; then
            pass "Vision published a valid detection schema (empty list is valid)"
          else
            fail "Vision did not publish a valid detection payload within 30 seconds"
            tail_log "$e2e_dir/vision.log" 100
          fi

          detect_response="$TEMP_ROOT/e2e_detect_result.json"
          subscribe_matching_background vision/detect_result "$detect_response" \
            15 session_id "$marker"
          detect_pid="$LAST_PID"
          sleep 0.7
          publish_json vision/detect \
            "{\"classes\":[\"person\"],\"min_confidence\":0.5,\"session_id\":\"$marker\"}" || true
          if wait_owned_pid "$detect_pid" && json_file_valid "$detect_response"; then
            pass "On-demand vision/detect request preserved its session ID"
          else
            fail "On-demand vision/detect request did not return valid JSON"
            tail_log "$e2e_dir/vision.log" 80
          fi
        else
          fail "Isolated Vision service exited early"
          tail_log "$e2e_dir/vision.log" 100
        fi
      else
        fail "Could not create isolated Vision configuration"
      fi
    fi
  else
    skip "Vision service E2E needs Pi camera/Hailo hardware (base E2E completed)"
  fi

  if ((RUN_LLM)); then
    if write_isolated_config "$APP_ROOT/llm_orchestrator/config.yaml" "$llm_config"; then
      run_llm_e2e "$e2e_dir" "$llm_config" "$marker"
    else
      fail "Could not create isolated LLM configuration"
    fi
  fi

  stop_services
  echo "E2E service logs: $e2e_dir"
}

load_local_api_key() {
  local env_file="$PROJECT_ROOT/.env.local"
  local key_line
  if [[ ! -f "$env_file" ]]; then
    return 1
  fi
  key_line="$(grep -m 1 '^DEEPSEEK_API_KEY=' "$env_file" 2>/dev/null || true)"
  if [[ -z "$key_line" ]]; then
    return 1
  fi
  export DEEPSEEK_API_KEY="${key_line#DEEPSEEK_API_KEY=}"
  [[ -n "$DEEPSEEK_API_KEY" ]]
}

run_llm_e2e() {
  local e2e_dir="$1"
  local llm_config="$2"
  local marker="$3"
  section "Explicit LLM E2E"
  echo "This mode may consume DeepSeek API credit when online routing is enabled."

  local prefer_online
  local proxy_pid=""
  local llm_pid
  local llm_ready=1
  prefer_online="$(read_yaml_value "$llm_config" router prefer_online || true)"

  if [[ "$prefer_online" == "true" ]]; then
    if load_local_api_key || [[ -n "${DEEPSEEK_API_KEY:-}" ]]; then
      pass "DeepSeek key is available without printing it"
    else
      fail "router.prefer_online=true but DEEPSEEK_API_KEY is unavailable"
      llm_ready=0
    fi
  else
    if curl -fsS --max-time 3 http://127.0.0.1:11434/api/tags \
        >"$TEMP_ROOT/ollama_tags.json" 2>/dev/null; then
      pass "CPU Ollama fallback is reachable on port 11434"
    else
      fail "Offline routing selected, but CPU Ollama is not reachable on port 11434"
      llm_ready=0
    fi

    if ! curl -fsS --max-time 3 -X POST http://127.0.0.1:8000/api/tags \
        >"$TEMP_ROOT/proxy_tags.json" 2>/dev/null; then
      start_service hailo_proxy llm_orchestrator.hailo_ollama_proxy \
        "$e2e_dir/hailo_proxy.log"
      proxy_pid="$LAST_PID"
      sleep 3
      if kill -0 "$proxy_pid" 2>/dev/null; then
        pass "Started project Hailo/Ollama proxy"
      else
        fail "Hailo/Ollama proxy exited early"
        tail_log "$e2e_dir/hailo_proxy.log" 60
        llm_ready=0
      fi
    else
      pass "Existing Hailo/Ollama service is reachable on port 8000"
    fi
  fi

  if ((llm_ready == 0)); then
    skip "LLM request skipped because its selected backend is not ready"
    return
  fi

  start_configured_service llm llm_orchestrator.main LLMOrchestrator \
    "$llm_config" "$e2e_dir/llm.log"
  llm_pid="$LAST_PID"
  sleep 3
  if ! kill -0 "$llm_pid" 2>/dev/null; then
    fail "LLM Orchestrator exited early"
    tail_log "$e2e_dir/llm.log" 80
    return
  fi
  pass "LLM Orchestrator stayed running"

  local response_payload="$TEMP_ROOT/e2e_llm_response.json"
  local response_pid
  subscribe_matching_background response/out "$response_payload" 150 \
    session_id "$marker"
  response_pid="$LAST_PID"
  sleep 0.7
  publish_json 'voice/transcript' \
    "{\"text\":\"Reply only PI5_LLM_OK. Do not call any tools.\",\"session_id\":\"$marker\"}" || true
  if wait_owned_pid "$response_pid" && \
      json_field_nonempty "$response_payload" text && \
      ! grep -q "having trouble completing that request" "$response_payload"; then
    pass "Session -> LLM -> response/out returned non-empty text"
  else
    fail "LLM E2E did not return a successful response"
    tail_log "$e2e_dir/llm.log" 100
  fi

  if [[ "$prefer_online" == "false" ]]; then
    local npu_response="$TEMP_ROOT/e2e_npu_response.json"
    if curl -fsS --max-time 150 \
        http://127.0.0.1:8000/api/chat \
        -H 'Content-Type: application/json' \
        -d '{"model":"qwen2.5:1.5b","messages":[{"role":"user","content":"Reply only PI5_NPU_OK"}],"stream":false}' \
        -o "$npu_response" && json_field_nonempty "$npu_response" message.content; then
      pass "No-tools request returned through the Hailo NPU endpoint"
    else
      fail "Direct no-tools Hailo NPU request failed"
      tail_log "$e2e_dir/hailo_proxy.log" 100
    fi
  fi
}

run_servo_test() {
  section "Explicit physical servo test"
  if ((IS_PI5 == 0)); then
    skip "Servo test requires Raspberry Pi 5 hardware"
    return
  fi
  cat <<'EOF'
SAFETY REQUIREMENTS:
  - Position servos (FS90), not continuous-rotation FS90R.
  - External regulated 5 V servo supply; do not power two loaded servos from Pi 5 V.
  - External supply ground connected to Raspberry Pi ground.
  - Linkages removed or confirmed safe for centre +/- 5 degree movement.
  - Be ready to disconnect servo power immediately.
EOF
  if ! confirm_action "This test will physically move both axes."; then
    skip "Servo test cancelled"
    return
  fi

  if have pgrep && pgrep -f 'gpio_service[.]main' >/dev/null 2>&1; then
    fail "GPIO service is already running; stop run_all.sh before servo isolation"
    return
  fi

  local pwm_config="$APP_ROOT/gpio_service/config.yaml"
  local pwm_chip
  local servo_log="$LOG_DIR/pi5_selftest_servo_$RUN_STAMP.log"
  pwm_chip="$(read_yaml_value "$pwm_config" pwm chip || true)"
  if [[ -z "$pwm_chip" || ! -d "$pwm_chip" ]]; then
    fail "Configured PWM controller is unavailable: ${pwm_chip:-unset}"
    return
  fi

  local -a pwm_channels=()
  mapfile -t pwm_channels < <("$PYTHON_BIN" - "$pwm_config" <<'PY'
import sys
import yaml

with open(sys.argv[1], encoding="utf-8") as stream:
    config = yaml.safe_load(stream)
channel_map = {int(pin): int(channel) for pin, channel in config["pwm"]["gpio_to_channel"].items()}
for servo in config["servo"].values():
    print(channel_map[int(servo["pin"])])
PY
  )
  if ((${#pwm_channels[@]} != 2)); then
    fail "Could not resolve both servo PWM channels from config.yaml"
    return
  fi

  local direct_access=1
  local channel attribute
  for channel in "${pwm_channels[@]}"; do
    if [[ -d "$pwm_chip/pwm$channel" ]]; then
      for attribute in duty_cycle enable period; do
        [[ -w "$pwm_chip/pwm$channel/$attribute" ]] || direct_access=0
      done
    elif [[ ! -w "$pwm_chip/export" ]]; then
      direct_access=0
    fi
  done

  local -a servo_command
  if ((direct_access)); then
    servo_command=(env PYTHONPATH="$PROJECT_PYTHONPATH" "$PYTHON_BIN")
  elif have sudo && sudo -n true 2>/dev/null; then
    servo_command=(sudo -n env PYTHONPATH="$PROJECT_PYTHONPATH" "$PYTHON_BIN")
  else
    fail "PWM is not writable; configure permissions or run 'sudo -v' before --servo"
    return
  fi

  if "${servo_command[@]}" - "$pwm_config" >"$servo_log" 2>&1 <<'PY'
import json
from pathlib import Path
import sys
import time

import yaml
from gpio_service.servo_controller import ServoController

with open(sys.argv[1], encoding="utf-8") as stream:
    config = yaml.safe_load(stream)

controller = None
result = {}
try:
    controller = ServoController(config)

    def duty(servo_number):
        servo = controller._servos[f"servo{servo_number}"]
        path = Path(controller.pwm_chip) / f"pwm{servo['channel']}" / "duty_cycle"
        return int(path.read_text(encoding="ascii").strip())

    def enabled(servo_number):
        servo = controller._servos[f"servo{servo_number}"]
        path = Path(controller.pwm_chip) / f"pwm{servo['channel']}" / "enable"
        return path.read_text(encoding="ascii").strip() == "1"

    centres = {
        number: controller._servos[f"servo{number}"]["center_angle"]
        for number in (1, 2)
    }
    controller.set_angles(centres)
    time.sleep(1)
    for servo_number, label in ((1, "pan"), (2, "tilt")):
        low_angle = centres[servo_number] - 5
        high_angle = centres[servo_number] + 5
        controller.set_angle(servo_number, low_angle)
        time.sleep(1)
        low = duty(servo_number)
        controller.set_angle(servo_number, high_angle)
        time.sleep(1)
        high = duty(servo_number)
        if low == high:
            raise AssertionError(f"{label} duty cycle did not change")
        result[label] = {
            "low_angle": low_angle,
            "low_duty": low,
            "high_angle": high_angle,
            "high_duty": high,
        }
        controller.set_angle(servo_number, centres[servo_number])
        time.sleep(1)
    if not (enabled(1) and enabled(2)):
        raise AssertionError("one or both PWM channels are disabled")
    print(json.dumps(result, sort_keys=True))
finally:
    if controller is not None:
        try:
            centres = {
                number: controller._servos[f"servo{number}"]["center_angle"]
                for number in (1, 2)
            }
            controller.set_angles(centres)
            time.sleep(0.5)
        finally:
            controller.cleanup()
PY
  then
    pass "Servo controller initialized from configured GPIO-to-channel mapping"
    pass "Pan PWM duty cycle changed across centre +/- 5 degrees"
    pass "Tilt PWM duty cycle changed across centre +/- 5 degrees"
  else
    fail "Direct isolated servo controller test failed"
    tail_log "$servo_log" 100
    warn "Disconnect servo power if either axis is not in a safe position"
    return
  fi

  if have vcgencmd; then
    local throttled_after
    throttled_after="$(vcgencmd get_throttled 2>&1 || true)"
    if [[ "$throttled_after" == "throttled=0x0" ]]; then
      pass "No under-voltage/throttling flags after servo motion"
    else
      fail "Power problem after servo test: $throttled_after"
    fi
  fi

  echo "Servo log: $servo_log"
  warn "GPIO cleanup disables PWM after the test; support the camera mount before it relaxes"
}

if ((RUN_AUDIO)); then
  run_audio_test
fi

if ((RUN_E2E)); then
  run_e2e_test
fi

if ((RUN_SERVO)); then
  run_servo_test
fi

section "Summary"
printf 'PASS: %d\n' "$PASS_COUNT"
printf 'FAIL: %d\n' "$FAIL_COUNT"
printf 'WARN: %d\n' "$WARN_COUNT"
printf 'SKIP: %d\n' "$SKIP_COUNT"
echo "Full log: $LOG_FILE"

if ((FAIL_COUNT > 0)); then
  echo "RESULT: FAIL"
  exit 1
fi

echo "RESULT: PASS"
exit 0
