#!/usr/bin/env bash
set -euo pipefail

# newclient.sh — создание папки клиента + копирование env-шаблона
#
# Usage:
#   ./scripts/newclient.sh <client> [--from <template_env>]
#
# По умолчанию шаблон берём из /root/clients/test_client2/.env

usage() {
  cat <<'USAGE'
newclient.sh — создать нового клиента (папка + env)

Usage:
  ./scripts/newclient.sh <client> [--from <template_env>]

Defaults:
  CLIENTS_DIR=/root/clients
  TEMPLATE_ENV=/root/clients/test_client2/.env
USAGE
}

CLIENTS_DIR="${CLIENTS_DIR:-/root/clients}"
TEMPLATE_ENV="${TEMPLATE_ENV:-/root/clients/test_client2/.env}"

CLIENT="${1:-}"
if [[ -z "${CLIENT}" || "${CLIENT}" == "help" || "${CLIENT}" == "-h" || "${CLIENT}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ "${2:-}" == "--from" ]]; then
  TEMPLATE_ENV="${3:-}"
  if [[ -z "${TEMPLATE_ENV}" ]]; then
    echo "ERROR: --from requires a path to template env" >&2
    exit 2
  fi
fi

if [[ ! -f "${TEMPLATE_ENV}" ]]; then
  echo "ERROR: template env not found: ${TEMPLATE_ENV}" >&2
  exit 2
fi

DEST_DIR="${CLIENTS_DIR}/${CLIENT}"
DEST_ENV="${DEST_DIR}/.env"

mkdir -p "${DEST_DIR}"

if [[ -f "${DEST_ENV}" ]]; then
  echo "ERROR: destination env already exists: ${DEST_ENV}" >&2
  exit 2
fi

cp "${TEMPLATE_ENV}" "${DEST_ENV}"

# Автоподстановка INSTANCE_ID
if grep -qE '^INSTANCE_ID=' "${DEST_ENV}"; then
  sed -i "s/^INSTANCE_ID=.*/INSTANCE_ID=${CLIENT}/" "${DEST_ENV}"
else
  echo "INSTANCE_ID=${CLIENT}" >> "${DEST_ENV}"
fi

echo
echo "Created: ${DEST_ENV}"
echo "Next: edit it (BOT_TOKEN, INSTANCE_NAME, admin ids, prices) ->"
echo "  nano ${DEST_ENV}"
