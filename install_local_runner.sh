#!/usr/bin/env bash
set -euo pipefail

LABEL="com.kan.local_runner"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE_PLIST="${ROOT_DIR}/com.kan.local_runner.plist"
LAUNCH_AGENTS_DIR="${HOME}/Library/LaunchAgents"
TARGET_PLIST="${LAUNCH_AGENTS_DIR}/${LABEL}.plist"
RUN_LOG_DIR="${ROOT_DIR}/.run/local_runner/logs"
STDOUT_LOG="${RUN_LOG_DIR}/local_runner.out.log"
STDERR_LOG="${RUN_LOG_DIR}/local_runner.err.log"
GUI_DOMAIN="gui/$(id -u)"

PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
if [[ ! -x "${PYTHON_BIN}" ]]; then
  PYTHON_BIN="$(command -v python3 || true)"
fi
if [[ -z "${PYTHON_BIN}" ]]; then
  echo "[fail] No se encontro python3 ni .venv/bin/python" >&2
  exit 1
fi

_sed_escape() {
  printf '%s' "$1" | sed -e 's/[\/&]/\\&/g'
}

_load_env_preview() {
  "${PYTHON_BIN}" - "${ROOT_DIR}" <<'PY'
import sys
from pathlib import Path

try:
    from dotenv import dotenv_values
except Exception:
    print("[warn] python-dotenv no disponible; omitiendo verificacion de .env")
    raise SystemExit(0)

root = Path(sys.argv[1]).resolve()
values = {}
for name in (".env", ".env.secrets"):
    env_file = root / name
    if env_file.exists():
        values.update({k: v for k, v in dotenv_values(env_file).items() if v is not None})

required = ("WS_URL", "CLIENT_ID", "RUNNER_TOKEN")
for key in required:
    if not str(values.get(key) or "").strip():
        print(f"[warn] {key} no esta visible desde .env/.env.secrets")
PY
}

install_agent() {
  if [[ ! -f "${TEMPLATE_PLIST}" ]]; then
    echo "[fail] No existe plantilla: ${TEMPLATE_PLIST}" >&2
    exit 1
  fi

  mkdir -p "${LAUNCH_AGENTS_DIR}" "${RUN_LOG_DIR}"
  : > "${STDOUT_LOG}"
  : > "${STDERR_LOG}"

  local root_esc py_esc out_esc err_esc
  root_esc="$(_sed_escape "${ROOT_DIR}")"
  py_esc="$(_sed_escape "${PYTHON_BIN}")"
  out_esc="$(_sed_escape "${STDOUT_LOG}")"
  err_esc="$(_sed_escape "${STDERR_LOG}")"

  sed \
    -e "s/__ROOT_DIR__/${root_esc}/g" \
    -e "s/__PYTHON_BIN__/${py_esc}/g" \
    -e "s/__STDOUT_PATH__/${out_esc}/g" \
    -e "s/__STDERR_PATH__/${err_esc}/g" \
    "${TEMPLATE_PLIST}" > "${TARGET_PLIST}"

  _load_env_preview

  launchctl bootout "${GUI_DOMAIN}" "${TARGET_PLIST}" >/dev/null 2>&1 || true
  launchctl bootstrap "${GUI_DOMAIN}" "${TARGET_PLIST}"
  launchctl kickstart -k "${GUI_DOMAIN}/${LABEL}" >/dev/null 2>&1 || true

  echo "[ok] LaunchAgent instalado: ${TARGET_PLIST}"
  echo "[ok] stdout: ${STDOUT_LOG}"
  echo "[ok] stderr: ${STDERR_LOG}"
}

uninstall_agent() {
  launchctl bootout "${GUI_DOMAIN}" "${TARGET_PLIST}" >/dev/null 2>&1 || true
  rm -f "${TARGET_PLIST}"
  echo "[ok] LaunchAgent desinstalado: ${LABEL}"
}

status_agent() {
  if [[ -f "${TARGET_PLIST}" ]]; then
    echo "[ok] plist instalado: ${TARGET_PLIST}"
  else
    echo "[down] plist no instalado"
  fi
  if launchctl print "${GUI_DOMAIN}/${LABEL}" >/dev/null 2>&1; then
    echo "[ok] launchctl: loaded"
  else
    echo "[down] launchctl: not loaded"
  fi
  echo "[info] logs:"
  echo "  ${STDOUT_LOG}"
  echo "  ${STDERR_LOG}"
}

usage() {
  cat <<EOF
Usage: ./install_local_runner.sh <install|uninstall|status>
EOF
}

cmd="${1:-install}"
case "${cmd}" in
  install) install_agent ;;
  uninstall) uninstall_agent ;;
  status) status_agent ;;
  *) usage; exit 1 ;;
esac

