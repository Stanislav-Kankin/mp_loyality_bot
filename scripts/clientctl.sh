#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
clientctl.sh — управление клиентскими инстансами (bot + worker + postgres)

Команды:
  help
  ps <client>
  up <client>
  rebuild <client>
  down <client>
  restart <client> [service...]
  logs <client> [service] [--tail N]
  env <client>
  exec <client> <cmd...>

Переменные окружения (опционально):
  MP_REPO_DIR        (default: /root/mp_loyality_bot)
  CLIENTS_DIR        (default: /root/clients)
  CLIENT_COMPOSE     (default: $MP_REPO_DIR/docker-compose.client.yml)
  CENTRAL_NET_NAME   (default: mp_central_net)
USAGE
}

MP_REPO_DIR="${MP_REPO_DIR:-/root/mp_loyality_bot}"
CLIENTS_DIR="${CLIENTS_DIR:-/root/clients}"
CLIENT_COMPOSE="${CLIENT_COMPOSE:-${MP_REPO_DIR}/docker-compose.client.yml}"
CENTRAL_NET_NAME="${CENTRAL_NET_NAME:-mp_central_net}"

CMD="${1:-help}"
CLIENT="${2:-}"

need_client() {
  if [[ -z "${CLIENT}" ]]; then
    echo "ERROR: client is required" >&2
    usage
    exit 2
  fi
}

env_file_for() {
  local c="$1"
  echo "${CLIENTS_DIR}/${c}/.env"
}

run_compose() {
  # run_compose <client> <docker-compose args...>
  local c="$1"
  shift || true

  local env_file
  env_file="$(env_file_for "${c}")"
  if [[ ! -f "${env_file}" ]]; then
    echo "ERROR: env file not found: ${env_file}" >&2
    exit 2
  fi

  CLIENT_ENV_FILE="${env_file}" CENTRAL_NET_NAME="${CENTRAL_NET_NAME}" \
    docker compose -f "${CLIENT_COMPOSE}" -p "${c}" "$@"
}

case "${CMD}" in
  help|-h|--help)
    usage
    ;;
  ps)
    need_client
    run_compose "${CLIENT}" ps
    ;;
  up)
    need_client
    run_compose "${CLIENT}" up -d --build
    ;;
  rebuild)
    need_client
    run_compose "${CLIENT}" up -d --build --force-recreate
    ;;
  down)
    need_client
    run_compose "${CLIENT}" down
    ;;
  restart)
    need_client
    shift 2 || true
    if [[ "$#" -eq 0 ]]; then
      set -- bot worker
    fi
    run_compose "${CLIENT}" restart "$@"
    ;;
  logs)
    need_client
    SERVICE="${3:-bot}"
    TAIL="200"
    if [[ "${4:-}" == "--tail" ]]; then
      TAIL="${5:-200}"
    fi
    run_compose "${CLIENT}" logs -f --tail="${TAIL}" "${SERVICE}"
    ;;
  env)
    need_client
    ENV_FILE="$(env_file_for "${CLIENT}")"
    grep -E '^(BOT_MODE|ADMIN_TG_IDS|INSTANCE_ID|INSTANCE_NAME|POSTGRES_DB|POSTGRES_USER|DATABASE_DSN|CENTRAL_DATABASE_DSN|HUB_BOT_USERNAME|CURRENCY|PRICE_PACK_1_MINOR|PRICE_PACK_3_MINOR|PRICE_PACK_10_MINOR)=' "${ENV_FILE}" || true
    ;;
  exec)
    need_client
    shift 2 || true
    if [[ "$#" -eq 0 ]]; then
      echo "ERROR: exec requires a command" >&2
      exit 2
    fi
    run_compose "${CLIENT}" exec -T bot "$@"
    ;;
  *)
    echo "ERROR: unknown cmd: ${CMD}" >&2
    usage
    exit 2
    ;;
esac
