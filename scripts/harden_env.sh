#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"
SECRETS_FILE="${ROOT_DIR}/.env.secrets"
STAMP="$(date +%Y%m%d_%H%M%S)"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "No existe ${ENV_FILE}"
  exit 1
fi

cp "${ENV_FILE}" "${ROOT_DIR}/.env.backup.${STAMP}"
if [[ -f "${SECRETS_FILE}" ]]; then
  cp "${SECRETS_FILE}" "${ROOT_DIR}/.env.secrets.backup.${STAMP}"
fi

touch "${SECRETS_FILE}"
chmod 600 "${SECRETS_FILE}" || true

tmp_env="$(mktemp)"
tmp_new_secrets="$(mktemp)"
tmp_merged="$(mktemp)"

cleanup() {
  rm -f "${tmp_env}" "${tmp_new_secrets}" "${tmp_merged}"
}
trap cleanup EXIT

awk '
function is_sensitive(k) {
  if (k ~ /(API_KEY|TOKEN|SECRET|PASSWORD|MASTER_KEY|DATABASE_URL|PRIVATE_KEY|ENCRYPTION_KEY)$/) return 1
  if (k ~ /^ALPACA_/) return 1
  if (k ~ /^META_/) return 1
  return 0
}
{
  line=$0
  if (line ~ /^[ \t]*#/ || line !~ /^[A-Za-z_][A-Za-z0-9_]*=/) {
    print line
    next
  }
  split(line, arr, "=")
  key=arr[1]
  value=substr(line, length(key)+2)
  if (is_sensitive(key)) {
    gsub(/^[ \t]+|[ \t]+$/, "", value)
    if (value != "" && value != "__IN_ENV_SECRETS__") {
      print key "=" value >> "'"${tmp_new_secrets}"'"
    }
    print key "=__IN_ENV_SECRETS__"
  } else {
    print line
  }
}
' "${ENV_FILE}" > "${tmp_env}"

cat "${SECRETS_FILE}" "${tmp_new_secrets}" | awk -F= '
  /^[A-Za-z_][A-Za-z0-9_]*=/ {
    key=$1
    val=substr($0, length(key)+2)
    vals[key]=val
    keys[++n]=key
    next
  }
  { extras[++m]=$0 }
  END {
    for (i=1; i<=m; i++) print extras[i]
    for (i=1; i<=n; i++) {
      k=keys[i]
      if (!seen[k]++) print k "=" vals[k]
    }
  }
' > "${tmp_merged}"

mv "${tmp_env}" "${ENV_FILE}"
mv "${tmp_merged}" "${SECRETS_FILE}"

echo "Listo:"
echo " - .env limpiado (secretos marcados como __IN_ENV_SECRETS__)"
echo " - secretos movidos a .env.secrets"
echo " - backups creados con sufijo ${STAMP}"
