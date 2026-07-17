#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
COMPOSE_FILE="$ROOT_DIR/docker-compose.test.yml"
PYTHON_BIN="${PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

export DJANGO_SETTINGS_MODULE=app.config.settings.test_fast
export DEBUG=False
export TEST_DATABASE_URL="${TEST_DATABASE_URL:-postgresql://querolink_test@127.0.0.1:${TEST_POSTGRES_PORT:-55432}/querolink_test}"
export TEST_REDIS_URL="${TEST_REDIS_URL:-redis://127.0.0.1:${TEST_REDIS_PORT:-56379}/0}"

die() {
    printf 'ERRO: %s\n' "$*" >&2
    exit 1
}

validate_safety() {
    "$PYTHON_BIN" - <<'PY'
import os
from urllib.parse import urlparse

url = os.environ["TEST_DATABASE_URL"]
parsed = urlparse(url)
host = (parsed.hostname or "").lower()
database = parsed.path.lstrip("/").lower()
if host not in {"127.0.0.1", "localhost", "test-postgres"}:
    raise SystemExit("TEST_DATABASE_URL recusada: host nao local")
if "test" not in database:
    raise SystemExit("TEST_DATABASE_URL recusada: banco sem 'test' no nome")
PY
}

start_services() {
    command -v docker >/dev/null 2>&1 || die "docker nao encontrado"
    docker compose -f "$COMPOSE_FILE" up -d --wait
}

run_tests() {
    validate_safety
    start_services
    cd "$ROOT_DIR"
    "$PYTHON_BIN" manage.py test --keepdb --parallel "${TEST_PROCESSES:-auto}" "$@"
}

usage() {
    printf '%s\n' \
        "Uso: scripts/test-fast.sh up|down|check|run [labels...]|full" \
        "  run    executa somente os labels informados" \
        "  full   executa a suite completa com banco reaproveitado"
}

command_name="${1:-}"
case "$command_name" in
    up)
        validate_safety
        start_services
        ;;
    down)
        docker compose -f "$COMPOSE_FILE" down --volumes --remove-orphans
        ;;
    check)
        validate_safety
        start_services
        cd "$ROOT_DIR"
        "$PYTHON_BIN" manage.py check
        ;;
    run)
        shift
        [ "$#" -gt 0 ] || die "informe ao menos um label de teste"
        run_tests "$@"
        ;;
    full)
        run_tests
        ;;
    *)
        usage
        exit 2
        ;;
esac
