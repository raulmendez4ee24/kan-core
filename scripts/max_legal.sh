#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -f "${ROOT_DIR}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${ROOT_DIR}/.env"
  set +a
fi

if [[ -x "${ROOT_DIR}/.venv/bin/python" ]]; then
  PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
else
  PYTHON_BIN="python3"
fi

usage() {
  cat <<EOF
Usage: scripts/max_legal.sh <apply|check|start>

  apply  Apply max-legal autonomy profile into .env
  check  Run backend doctor to validate profile
  start  Apply profile, validate, start stack, and run smoke test
EOF
}

cmd="${1:-start}"
case "${cmd}" in
  apply)
    "${PYTHON_BIN}" "${ROOT_DIR}/scripts/apply_max_legal_profile.py"
    ;;
  check)
    "${PYTHON_BIN}" "${ROOT_DIR}/scripts/backend_doctor.py"
    ;;
  start)
    "${PYTHON_BIN}" "${ROOT_DIR}/scripts/apply_max_legal_profile.py"
    "${PYTHON_BIN}" "${ROOT_DIR}/scripts/backend_doctor.py"
    "${ROOT_DIR}/scripts/full_auto_stack.sh" start
    "${ROOT_DIR}/scripts/full_auto_stack.sh" smoke
    ;;
  *)
    usage
    exit 1
    ;;
esac
