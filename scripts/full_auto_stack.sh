#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="${ROOT_DIR}/.run/full_auto"
LOG_DIR="${RUN_DIR}/logs"
PID_DIR="${RUN_DIR}/pids"
SERVICES=("backend" "desktop_agent" "execution_worker" "task_scheduler" "autonomous_runner")
NGROK_NAME="ngrok_static"

mkdir -p "${LOG_DIR}" "${PID_DIR}"

if [[ -f "${ROOT_DIR}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${ROOT_DIR}/.env"
  if [[ -f "${ROOT_DIR}/.env.secrets" ]]; then
    # shellcheck disable=SC1091
    source "${ROOT_DIR}/.env.secrets"
  fi
  set +a
fi

PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
if [[ ! -x "${PYTHON_BIN}" ]]; then
  PYTHON_BIN="python3"
fi

_pid_file() {
  local name="$1"
  echo "${PID_DIR}/${name}.pid"
}

_is_running() {
  local pid="$1"
  kill -0 "${pid}" >/dev/null 2>&1
}

_service_cmd() {
  local name="$1"
  case "${name}" in
    backend)
      echo "${PYTHON_BIN} -m uvicorn main:app --host 0.0.0.0 --port 8000"
      ;;
    desktop_agent)
      echo "env PYTHONPATH=. ${PYTHON_BIN} -m desktop_agent.agent_client"
      ;;
    execution_worker)
      echo "env PYTHONPATH=. ${PYTHON_BIN} workers/execution_worker.py"
      ;;
    task_scheduler)
      echo "env PYTHONPATH=. ${PYTHON_BIN} workers/task_scheduler.py"
      ;;
    autonomous_runner)
      echo "env PYTHONPATH=. ${PYTHON_BIN} workers/autonomous_runner.py"
      ;;
    *)
      return 1
      ;;
  esac
}

_ngrok_pid_file() {
  echo "${PID_DIR}/${NGROK_NAME}.pid"
}

_ngrok_base_url() {
  local raw="${NGROK_STATIC_DOMAIN:-}"
  raw="${raw%%/}"
  if [[ -z "${raw}" ]]; then
    return 1
  fi
  if [[ "${raw}" == http://* || "${raw}" == https://* ]]; then
    echo "${raw}"
  else
    echo "https://${raw}"
  fi
}

_start_ngrok_static() {
  if [[ "${ENABLE_NGROK_STATIC_DOMAIN:-false}" != "true" ]]; then
    return 0
  fi
  if ! command -v ngrok >/dev/null 2>&1; then
    echo "[warn] ngrok no instalado; omitiendo tunel estatico"
    return 0
  fi
  if [[ -z "${NGROK_AUTHTOKEN:-}" || -z "${NGROK_STATIC_DOMAIN:-}" || "${NGROK_AUTHTOKEN:-}" == "__IN_ENV_SECRETS__" || "${NGROK_STATIC_DOMAIN:-}" == "your-subdomain.ngrok-free.app" ]]; then
    echo "[warn] faltan NGROK_AUTHTOKEN o NGROK_STATIC_DOMAIN; omitiendo tunel estatico"
    return 0
  fi

  local pid_file
  pid_file="$(_ngrok_pid_file)"
  if [[ -f "${pid_file}" ]]; then
    local old_pid
    old_pid="$(cat "${pid_file}" 2>/dev/null || true)"
    if [[ -n "${old_pid}" ]] && _is_running "${old_pid}"; then
      echo "[skip] ${NGROK_NAME} ya esta corriendo (pid=${old_pid})"
      return 0
    fi
    rm -f "${pid_file}"
  fi

  ngrok config add-authtoken "${NGROK_AUTHTOKEN}" >/dev/null 2>&1 || true

  local base_url
  base_url="$(_ngrok_base_url)"
  echo "[start] ${NGROK_NAME} (${base_url})"
  cd "${ROOT_DIR}"
  nohup bash -lc "ngrok http --url=${base_url} 8000" >"${LOG_DIR}/${NGROK_NAME}.log" 2>&1 < /dev/null &
  local ngrok_pid=$!
  disown "${ngrok_pid}" 2>/dev/null || true
  echo "${ngrok_pid}" >"${pid_file}"
  if ! _await_service_alive "${NGROK_NAME}"; then
    echo "[fail] ${NGROK_NAME} no se mantuvo vivo. Ultimas lineas:"
    tail -n 30 "${LOG_DIR}/${NGROK_NAME}.log" 2>/dev/null || true
    return 1
  fi

  if [[ "${ENABLE_TELEGRAM_WEBHOOK_SETUP:-true}" == "true" && -n "${TELEGRAM_BOT_TOKEN:-}" ]]; then
    local webhook_url="${base_url}/webhooks/telegram"
    local resp
    if [[ -n "${TELEGRAM_SECRET_TOKEN:-}" ]]; then
      resp="$(curl -sS -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
        -H "Content-Type: application/json" \
        -d "{\"url\":\"${webhook_url}\",\"secret_token\":\"${TELEGRAM_SECRET_TOKEN}\"}" || true)"
    else
      resp="$(curl -sS -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
        -H "Content-Type: application/json" \
        -d "{\"url\":\"${webhook_url}\"}" || true)"
    fi
    export TELEGRAM_WEBHOOK_URL="${webhook_url}"
    echo "[info] webhook telegram configurado a ${webhook_url}"
    echo "[info] respuesta setWebhook: ${resp}"
  fi
}

_stop_ngrok_static() {
  local pid_file
  pid_file="$(_ngrok_pid_file)"
  if [[ ! -f "${pid_file}" ]]; then
    return 0
  fi
  local pid
  pid="$(cat "${pid_file}" 2>/dev/null || true)"
  if [[ -n "${pid}" ]] && _is_running "${pid}"; then
    echo "[stop] ${NGROK_NAME} (pid=${pid})"
    kill "${pid}" >/dev/null 2>&1 || true
  fi
  rm -f "${pid_file}"
}

_await_service_alive() {
  local name="$1"
  local attempts="${2:-25}"
  local delay="${3:-0.2}"
  local pid_file
  pid_file="$(_pid_file "${name}")"

  for _ in $(seq 1 "${attempts}"); do
    if [[ -f "${pid_file}" ]]; then
      local pid
      pid="$(cat "${pid_file}" 2>/dev/null || true)"
      if [[ -n "${pid}" ]] && _is_running "${pid}"; then
        return 0
      fi
    fi
    sleep "${delay}"
  done
  return 1
}

_backend_healthy() {
  curl -fsS --max-time 2 "http://127.0.0.1:8000/healthz" >/dev/null 2>&1
}

_await_backend_health() {
  for _ in $(seq 1 50); do
    if _backend_healthy; then
      return 0
    fi
    sleep 0.2
  done
  return 1
}

_start_service() {
  local name="$1"
  local pid_file
  pid_file="$(_pid_file "${name}")"
  if [[ -f "${pid_file}" ]]; then
    local old_pid
    old_pid="$(cat "${pid_file}" 2>/dev/null || true)"
    if [[ -n "${old_pid}" ]] && _is_running "${old_pid}"; then
      echo "[skip] ${name} already running (pid=${old_pid})"
      return 0
    fi
    rm -f "${pid_file}"
  fi

  echo "[start] ${name}"
  local cmd
  cmd="$(_service_cmd "${name}")"
  cd "${ROOT_DIR}"
  nohup bash -lc "${cmd}" >"${LOG_DIR}/${name}.log" 2>&1 < /dev/null &
  local service_pid=$!
  disown "${service_pid}" 2>/dev/null || true
  echo "${service_pid}" >"${pid_file}"
  if ! _await_service_alive "${name}"; then
    echo "[fail] ${name} did not stay running. Last log lines:"
    tail -n 30 "${LOG_DIR}/${name}.log" 2>/dev/null || true
    return 1
  fi
}

_stop_service() {
  local name="$1"
  local pid_file
  pid_file="$(_pid_file "${name}")"
  if [[ ! -f "${pid_file}" ]]; then
    echo "[skip] ${name} not tracked"
    return 0
  fi
  local pid
  pid="$(cat "${pid_file}" 2>/dev/null || true)"
  if [[ -z "${pid}" ]]; then
    rm -f "${pid_file}"
    echo "[skip] ${name} pid empty"
    return 0
  fi
  if _is_running "${pid}"; then
    echo "[stop] ${name} (pid=${pid})"
    kill "${pid}" >/dev/null 2>&1 || true
  else
    echo "[skip] ${name} already stopped"
  fi
  rm -f "${pid_file}"
}

status() {
  for name in "${SERVICES[@]}"; do
    local pid_file
    pid_file="$(_pid_file "${name}")"
    if [[ -f "${pid_file}" ]]; then
      local pid
      pid="$(cat "${pid_file}" 2>/dev/null || true)"
      if [[ -n "${pid}" ]] && _is_running "${pid}"; then
        echo "[ok] ${name} running (pid=${pid}) log=${LOG_DIR}/${name}.log"
      else
        echo "[down] ${name} (stale pid file)"
      fi
    else
      echo "[down] ${name} (not started)"
    fi
  done
  local ngrok_pid_file
  ngrok_pid_file="$(_ngrok_pid_file)"
  if [[ -f "${ngrok_pid_file}" ]]; then
    local ngrok_pid
    ngrok_pid="$(cat "${ngrok_pid_file}" 2>/dev/null || true)"
    if [[ -n "${ngrok_pid}" ]] && _is_running "${ngrok_pid}"; then
      echo "[ok] ${NGROK_NAME} running (pid=${ngrok_pid}) log=${LOG_DIR}/${NGROK_NAME}.log"
    else
      echo "[down] ${NGROK_NAME} (stale pid file)"
    fi
  else
    echo "[down] ${NGROK_NAME} (not started)"
  fi
}

start() {
  export ENABLE_DESKTOP_AGENT="${ENABLE_DESKTOP_AGENT:-true}"
  export ENABLE_DESKTOP_AUTONOMY_LOOP="${ENABLE_DESKTOP_AUTONOMY_LOOP:-true}"
  export ENABLE_ENTERPRISE_POLICIES="${ENABLE_ENTERPRISE_POLICIES:-true}"
  export ENABLE_ENTERPRISE_CONSOLE="${ENABLE_ENTERPRISE_CONSOLE:-true}"
  export ENABLE_EXECUTION_QUEUE="${ENABLE_EXECUTION_QUEUE:-true}"
  export ENABLE_TASK_SCHEDULER="${ENABLE_TASK_SCHEDULER:-true}"
  export ENABLE_TASK_STUDIO="${ENABLE_TASK_STUDIO:-true}"
  export ENABLE_AUTONOMOUS_RUNNER="${ENABLE_AUTONOMOUS_RUNNER:-true}"
  export ENABLE_DESKTOP_AUTO_APPROVAL="${ENABLE_DESKTOP_AUTO_APPROVAL:-true}"
  export DESKTOP_AUTO_APPROVAL_MAX_RISK="${DESKTOP_AUTO_APPROVAL_MAX_RISK:-high}"
  export ENABLE_BUSINESS_CONTEXT_REASONING="${ENABLE_BUSINESS_CONTEXT_REASONING:-true}"
  export AUTONOMY_DEFAULT_EXECUTION_MODE="${AUTONOMY_DEFAULT_EXECUTION_MODE:-full_auto}"
  export AUTONOMY_DEFAULT_MAX_AUTO_RISK="${AUTONOMY_DEFAULT_MAX_AUTO_RISK:-high}"
  export POLICY_AUTO_APPLY_PRESET="${POLICY_AUTO_APPLY_PRESET:-openclaw_allow_all}"

  _start_service "backend"
  if ! _await_backend_health; then
    echo "[fail] backend health check failed on /healthz"
    tail -n 50 "${LOG_DIR}/backend.log" 2>/dev/null || true
    return 1
  fi

  _start_service "desktop_agent"
  _start_service "execution_worker"
  _start_service "task_scheduler"
  _start_service "autonomous_runner"
  _start_ngrok_static

  echo ""
  echo "Full-auto stack started."
  status
}

stop() {
  _stop_service "autonomous_runner"
  _stop_service "task_scheduler"
  _stop_service "execution_worker"
  _stop_service "desktop_agent"
  _stop_service "backend"
  _stop_ngrok_static
  echo "Full-auto stack stopped."
}

heal() {
  local healed=0
  for name in "${SERVICES[@]}"; do
    local pid_file
    pid_file="$(_pid_file "${name}")"
    local running=0
    if [[ -f "${pid_file}" ]]; then
      local pid
      pid="$(cat "${pid_file}" 2>/dev/null || true)"
      if [[ -n "${pid}" ]] && _is_running "${pid}"; then
        running=1
      fi
    fi
    if [[ "${running}" -eq 0 ]]; then
      _start_service "${name}"
      healed=$((healed + 1))
    fi
  done
  if [[ "${ENABLE_NGROK_STATIC_DOMAIN:-false}" == "true" ]]; then
    _start_ngrok_static
  fi
  if [[ "${healed}" -eq 0 ]]; then
    echo "No healing required. All services running."
  else
    echo "Healed ${healed} service(s)."
  fi
  status
}

smoke() {
  if ! _backend_healthy; then
    echo "[fail] backend not healthy; run: scripts/full_auto_stack.sh start"
    return 1
  fi
  if [[ -z "${KAN_CLIENT_ID:-}" || -z "${KAN_CLIENT_TOKEN:-}" ]]; then
    echo "[fail] KAN_CLIENT_ID / KAN_CLIENT_TOKEN missing in .env"
    return 1
  fi
  local resp
  resp="$(curl -fsS -X POST "http://127.0.0.1:8000/desktop-autonomy/research-run" \
    -H "Content-Type: application/json" \
    -H "X-Client-Id: ${KAN_CLIENT_ID}" \
    -H "X-Client-Token: ${KAN_CLIENT_TOKEN}" \
    -d '{"goal":"Smoke test autonomia","dry_run":true,"require_verified_sources":false,"enforce_quality_gate":false,"dynamic_risk_controls":false}')" || {
      echo "[fail] smoke request failed"
      return 1
    }
  if [[ -z "${resp}" ]]; then
    echo "[fail] smoke response empty"
    return 1
  fi
  SMOKE_RESP="${resp}" python3 - <<'PY'
import json
import os
obj = json.loads(os.environ["SMOKE_RESP"])
print(f"status={obj.get('status')} execution_status={(obj.get('execution') or {}).get('status')}")
PY
}

gate() {
  if ! _backend_healthy; then
    echo "[fail] backend not healthy; run: scripts/full_auto_stack.sh start"
    return 1
  fi
  if [[ -z "${KAN_CLIENT_ID:-}" || -z "${KAN_CLIENT_TOKEN:-}" ]]; then
    echo "[fail] KAN_CLIENT_ID / KAN_CLIENT_TOKEN missing in .env"
    return 1
  fi
  local report_path="${WEEKLY_EVAL_REPORT_PATH:-${ROOT_DIR}/.run/weekly_eval_report.json}"
  local history_path="${WEEKLY_EVAL_HISTORY_PATH:-${ROOT_DIR}/.run/weekly_eval_history.jsonl}"

  echo "[gate] running weekly eval harness"
  if ! env PYTHONPATH=. "${PYTHON_BIN}" "${ROOT_DIR}/scripts/weekly_eval_harness.py" \
    --base-url "http://127.0.0.1:8000" \
    --client-id "${KAN_CLIENT_ID}" \
    --token "${KAN_CLIENT_TOKEN}" \
    --out "${report_path}" \
    --history-out "${history_path}"; then
    echo "[gate] weekly eval returned non-zero; checking release guard details"
  fi

  echo "[gate] running release guard"
  env PYTHONPATH=. "${PYTHON_BIN}" "${ROOT_DIR}/scripts/release_guard.py" \
    --history "${history_path}" \
    --latest-report "${report_path}"

  echo "[gate] fetching ops health"
  curl -fsS \
    -H "Authorization: Bearer ${KAN_CLIENT_TOKEN}" \
    "http://127.0.0.1:8000/desktop-agent/ops/health?window_minutes=${DESKTOP_OPS_DEFAULT_WINDOW_MINUTES:-60}" || {
      echo "[warn] unable to fetch /desktop-agent/ops/health"
      return 0
    }
  echo ""
  echo "[ok] deploy gate passed"
}

logs() {
  local name="${1:-backend}"
  if [[ "${name}" == "all" ]]; then
    for svc in "${SERVICES[@]}"; do
      echo "=== ${svc} ==="
      tail -n 20 "${LOG_DIR}/${svc}.log" 2>/dev/null || true
      echo ""
    done
    echo "=== ${NGROK_NAME} ==="
    tail -n 20 "${LOG_DIR}/${NGROK_NAME}.log" 2>/dev/null || true
    echo ""
    return 0
  fi
  local file="${LOG_DIR}/${name}.log"
  if [[ ! -f "${file}" ]]; then
    echo "No log file for ${name}: ${file}"
    exit 1
  fi
  tail -n 80 -f "${file}"
}

usage() {
  cat <<EOF
Usage: scripts/full_auto_stack.sh <start|stop|status|heal|smoke|logs [service]>

Services:
  backend
  desktop_agent
  execution_worker
  task_scheduler
  autonomous_runner
  all (for logs)
Commands:
  gate   run weekly eval + release guard + ops health check
EOF
}

cmd="${1:-status}"
case "${cmd}" in
  start) start ;;
  stop) stop ;;
  status) status ;;
  heal) heal ;;
  smoke) smoke ;;
  gate) gate ;;
  logs) shift; logs "${1:-backend}" ;;
  *) usage; exit 1 ;;
esac
