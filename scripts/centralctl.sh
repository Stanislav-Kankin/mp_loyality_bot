#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
centralctl.sh — управление central стеком (central_postgres + superadmin + payment_hub)

Команды:
  help
  ps
  up
  rebuild
  down
  restart <service>
  logs <service> [--tail N]

Сервисы обычно: central_postgres | superadmin | payment_hub

Переменные окружения (опционально):
  MP_REPO_DIR        (default: /root/mp_loyality_bot)
  HUB_ENV_FILE       (default: /root/clients/payment_hub/.env)
  CENTRAL_NET_NAME   (default: mp_central_net)
USAGE
}

MP_REPO_DIR="${MP_REPO_DIR:-/root/mp_loyality_bot}"
CENTRAL_COMPOSE="${CENTRAL_COMPOSE:-${MP_REPO_DIR}/docker-compose.central.yml}"
HUB_ENV_FILE="${HUB_ENV_FILE:-/root/clients/payment_hub/.env}"
CENTRAL_NET_NAME="${CENTRAL_NET_NAME:-mp_central_net}"

CMD="${1:-help}"
SERVICE="${2:-}"

run_compose() {
  HUB_ENV_FILE="${HUB_ENV_FILE}" CENTRAL_NET_NAME="${CENTRAL_NET_NAME}" \
    docker compose -f "${CENTRAL_COMPOSE}" "$@"
}

case "${CMD}" in
  help|-h|--help)
    usage
    ;;
  ps)
    run_compose ps
    ;;
  up)
    run_compose up -d --build
    ;;
  rebuild)
    run_compose up -d --build --force-recreate
    ;;
  down)
    run_compose down
    ;;
  restart)
    if [[ -z "${SERVICE}" ]]; then
      echo "ERROR: restart requires service name" >&2
      exit 2
    fi
    run_compose restart "${SERVICE}"
    ;;
  logs)
    if [[ -z "${SERVICE}" ]]; then
      echo "ERROR: logs requires service name" >&2
      exit 2
    fi
    TAIL="200"
    if [[ "${3:-}" == "--tail" ]]; then
      TAIL="${4:-200}"
    fi
    run_compose logs -f --tail="${TAIL}" "${SERVICE}"
    ;;
  *)
    echo "ERROR: unknown cmd: ${CMD}" >&2
    usage
    exit 2
    ;;
esac
