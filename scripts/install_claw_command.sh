#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_CMD="${ROOT_DIR}/scripts/claw"
TARGET_DIR="${HOME}/.local/bin"
TARGET_CMD="${TARGET_DIR}/claw"
SHELL_RC="${HOME}/.zshrc"
PATH_LINE='export PATH="$HOME/.local/bin:$PATH"'

mkdir -p "${TARGET_DIR}"
ln -sf "${SOURCE_CMD}" "${TARGET_CMD}"
chmod +x "${SOURCE_CMD}"

if [[ -f "${SHELL_RC}" ]]; then
  if ! grep -Fq "${PATH_LINE}" "${SHELL_RC}"; then
    {
      echo ""
      echo "# Added by chatbotn8n claw installer"
      echo "${PATH_LINE}"
    } >> "${SHELL_RC}"
  fi
fi

echo "Installed command: ${TARGET_CMD}"
echo "If 'claw' is not found yet, run:"
echo "  source ${SHELL_RC}"
echo "or open a new terminal."
