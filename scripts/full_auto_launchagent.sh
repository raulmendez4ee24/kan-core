#!/usr/bin/env bash
set -euo pipefail

LABEL="com.kan-core.fullauto"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LAUNCH_AGENTS_DIR="${HOME}/Library/LaunchAgents"
SUPPORT_DIR="${HOME}/Library/Application Support/kan-core"
LAUNCHER_PATH="${SUPPORT_DIR}/full_auto_boot.sh"
PLIST_PATH="${LAUNCH_AGENTS_DIR}/${LABEL}.plist"
RUN_LOG="${ROOT_DIR}/.run/full_auto/launchagent.out.log"
ERR_LOG="${ROOT_DIR}/.run/full_auto/launchagent.err.log"
GUI_DOMAIN="gui/$(id -u)"

mkdir -p "${LAUNCH_AGENTS_DIR}"
mkdir -p "${ROOT_DIR}/.run/full_auto"
mkdir -p "${SUPPORT_DIR}"

install_agent() {
  cat >"${LAUNCHER_PATH}" <<EOF
#!/usr/bin/env bash
set -euo pipefail
/usr/bin/osascript <<OSA
tell application "Terminal"
  do script "cd '${ROOT_DIR}' && set -a; source .env; set +a; scripts/full_auto_stack.sh start"
  activate
end tell
OSA
exit 0
EOF
  chmod +x "${LAUNCHER_PATH}"

  cat >"${PLIST_PATH}" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>${LAUNCHER_PATH}</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${RUN_LOG}</string>
  <key>StandardErrorPath</key>
  <string>${ERR_LOG}</string>
</dict>
</plist>
EOF

  launchctl bootout "${GUI_DOMAIN}" "${PLIST_PATH}" >/dev/null 2>&1 || true
  launchctl bootstrap "${GUI_DOMAIN}" "${PLIST_PATH}"
  launchctl kickstart -k "${GUI_DOMAIN}/${LABEL}" >/dev/null 2>&1 || true
  echo "Installed and started LaunchAgent: ${LABEL}"
}

uninstall_agent() {
  launchctl bootout "${GUI_DOMAIN}" "${PLIST_PATH}" >/dev/null 2>&1 || true
  rm -f "${PLIST_PATH}"
  rm -f "${LAUNCHER_PATH}"
  echo "Uninstalled LaunchAgent: ${LABEL}"
}

status_agent() {
  if [[ -f "${PLIST_PATH}" ]]; then
    echo "plist: installed (${PLIST_PATH})"
  else
    echo "plist: not installed"
  fi
  launchctl print "${GUI_DOMAIN}/${LABEL}" >/dev/null 2>&1 && echo "launchctl: loaded" || echo "launchctl: not loaded"
  "${ROOT_DIR}/scripts/full_auto_stack.sh" status
}

usage() {
  cat <<EOF
Usage: scripts/full_auto_launchagent.sh <install|uninstall|status>
EOF
}

cmd="${1:-status}"
case "${cmd}" in
  install) install_agent ;;
  uninstall) uninstall_agent ;;
  status) status_agent ;;
  *) usage; exit 1 ;;
esac
