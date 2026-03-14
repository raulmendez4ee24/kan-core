#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="${ROOT_DIR}/.run/wa_qr"
PID_FILE="${RUN_DIR}/bridge.pid"
LOG_FILE="${RUN_DIR}/bridge.log"
BRIDGE_CMD="${ROOT_DIR}/whatsapp_qr_bridge/bridge.js"

mkdir -p "${RUN_DIR}"

running_pids() {
  pgrep -f "${BRIDGE_CMD}" || true
}

primary_pid() {
  running_pids | tail -n 1
}

start() {
  local live_pid
  live_pid="$(primary_pid)"
  if [[ -n "${live_pid}" ]]; then
    echo "${live_pid}" >"${PID_FILE}"
    echo "[wa-qr] already running (pid=${live_pid})"
    exit 0
  fi

  if [[ -f "${PID_FILE}" ]]; then
    local pid
    pid="$(cat "${PID_FILE}" 2>/dev/null || true)"
    if [[ -n "${pid}" ]] && kill -0 "${pid}" >/dev/null 2>&1; then
      echo "[wa-qr] already running (pid=${pid})"
      exit 0
    fi
    rm -f "${PID_FILE}"
  fi

  if [[ ! -d "${ROOT_DIR}/whatsapp_qr_bridge/node_modules" ]]; then
    echo "[wa-qr] installing dependencies..."
    (cd "${ROOT_DIR}/whatsapp_qr_bridge" && npm install)
  fi

  echo "[wa-qr] starting..."
  (
    cd "${ROOT_DIR}/whatsapp_qr_bridge"
    export WA_DATA_PATH="${WA_DATA_PATH:-${ROOT_DIR}/.run/wa_qr_runtime}"
    export WA_CLIENT_ID="${WA_CLIENT_ID:-kan-whatsapp-qr-$(date +%s)}"
    nohup node "${BRIDGE_CMD}" >>"${LOG_FILE}" 2>&1 &
    echo $! >"${PID_FILE}"
  )
  sleep 1
  local pid
  pid="$(cat "${PID_FILE}")"
  if kill -0 "${pid}" >/dev/null 2>&1; then
    echo "[wa-qr] started (pid=${pid}) log=${LOG_FILE}"
  else
    echo "[wa-qr] failed to start, revisa log: ${LOG_FILE}"
    tail -n 40 "${LOG_FILE}" || true
    rm -f "${PID_FILE}"
    exit 1
  fi
  echo "[wa-qr] usa: scripts/whatsapp_qr_bridge.sh logs"
}

pair() {
  local phone="${2:-}"
  if [[ -z "${phone}" ]]; then
    echo "Uso: scripts/whatsapp_qr_bridge.sh pair <telefono_internacional>"
    echo "Ejemplo MX: scripts/whatsapp_qr_bridge.sh pair 5215512345678"
    exit 1
  fi
  if [[ ! -d "${ROOT_DIR}/whatsapp_qr_bridge/node_modules" ]]; then
    echo "[wa-qr] installing dependencies..."
    (cd "${ROOT_DIR}/whatsapp_qr_bridge" && npm install)
  fi
  echo "[wa-qr] pair mode (foreground). Presiona Ctrl+C para salir."
  (
    cd "${ROOT_DIR}/whatsapp_qr_bridge"
    export WA_PAIR_PHONE="${phone}"
    export WA_HEADLESS="${WA_HEADLESS:-false}"
    export WA_CLIENT_ID="${WA_CLIENT_ID:-kan-whatsapp-pair-$(date +%s)}"
    node "${BRIDGE_CMD}"
  )
}

stop() {
  local pids
  pids="$(running_pids)"
  if [[ -z "${pids}" ]]; then
    if [[ -f "${PID_FILE}" ]]; then
      echo "[wa-qr] pid stale"
    else
      echo "[wa-qr] not running"
    fi
    rm -f "${PID_FILE}"
    exit 0
  fi
  # Kill all bridge instances to avoid orphan/stale sessions.
  while IFS= read -r pid; do
    [[ -z "${pid}" ]] && continue
    kill "${pid}" >/dev/null 2>&1 || true
    echo "[wa-qr] stopped (pid=${pid})"
  done <<<"${pids}"
  rm -f "${PID_FILE}"
}

status() {
  local pid
  pid="$(primary_pid)"
  if [[ -n "${pid}" ]]; then
    echo "${pid}" >"${PID_FILE}"
    echo "[wa-qr] running (pid=${pid}) log=${LOG_FILE}"
    exit 0
  fi
  echo "[wa-qr] down"
}

logs() {
  touch "${LOG_FILE}"
  tail -n 120 -f "${LOG_FILE}"
}

case "${1:-status}" in
  start|up) start ;;
  pair) pair "$@" ;;
  stop|down) stop ;;
  status) status ;;
  logs) logs ;;
  *)
    echo "Usage: scripts/whatsapp_qr_bridge.sh <start|pair|stop|status|logs>"
    exit 1
    ;;
esac
