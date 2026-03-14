#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
if [[ ! -x "${PYTHON_BIN}" ]]; then
  PYTHON_BIN="python3"
fi

HISTORY_PATH="${WEEKLY_EVAL_HISTORY_PATH:-${ROOT_DIR}/.run/weekly_eval_history.jsonl}"
REPORT_PATH="${WEEKLY_EVAL_REPORT_PATH:-${ROOT_DIR}/.run/weekly_eval_report.json}"
HEALTHCHECK_URL="${CANARY_HEALTHCHECK_URL:-}"
HEALTHCHECK_TIMEOUT_SECONDS="${CANARY_HEALTHCHECK_TIMEOUT_SECONDS:-180}"
HEALTHCHECK_INTERVAL_SECONDS="${CANARY_HEALTHCHECK_INTERVAL_SECONDS:-5}"
DEPLOY_CANARY_CMD="${DEPLOY_CANARY_CMD:-}"
ROLLBACK_CANARY_CMD="${ROLLBACK_CANARY_CMD:-}"

if [[ -z "${DEPLOY_CANARY_CMD}" ]]; then
  echo "[fail] DEPLOY_CANARY_CMD is required"
  exit 2
fi
if [[ -z "${ROLLBACK_CANARY_CMD}" ]]; then
  echo "[fail] ROLLBACK_CANARY_CMD is required"
  exit 2
fi
if [[ -z "${HEALTHCHECK_URL}" ]]; then
  echo "[fail] CANARY_HEALTHCHECK_URL is required"
  exit 2
fi

rollback() {
  echo "[rollback] executing rollback command"
  bash -lc "${ROLLBACK_CANARY_CMD}" || true
}

release_guard() {
  env PYTHONPATH=. "${PYTHON_BIN}" "${ROOT_DIR}/scripts/release_guard.py" \
    --history "${HISTORY_PATH}" \
    --latest-report "${REPORT_PATH}" \
    --require-report true
}

await_health() {
  local started
  started="$(date +%s)"
  while true; do
    if curl -fsS --max-time 5 "${HEALTHCHECK_URL}" >/dev/null 2>&1; then
      echo "[ok] canary healthcheck passed: ${HEALTHCHECK_URL}"
      return 0
    fi
    local now
    now="$(date +%s)"
    if (( now - started >= HEALTHCHECK_TIMEOUT_SECONDS )); then
      echo "[fail] canary healthcheck timeout after ${HEALTHCHECK_TIMEOUT_SECONDS}s"
      return 1
    fi
    sleep "${HEALTHCHECK_INTERVAL_SECONDS}"
  done
}

echo "[gate] pre-deploy release guard"
if ! release_guard; then
  echo "[block] release guard blocked deploy before canary"
  exit 2
fi

echo "[deploy] starting canary deploy"
if ! bash -lc "${DEPLOY_CANARY_CMD}"; then
  echo "[fail] canary deploy command failed"
  rollback
  exit 3
fi

if ! await_health; then
  rollback
  exit 4
fi

echo "[gate] post-deploy release guard"
if ! release_guard; then
  echo "[block] release guard blocked release after canary; rolling back"
  rollback
  exit 5
fi

echo "[ok] canary deploy completed and gate passed"
