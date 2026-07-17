#!/bin/bash
set -Eeuo pipefail

# ============================================================
# QueroLink — restore_to_test.sh
# Restaura backup (dump + midia + manifesto) em ambiente de
# teste vazio. NUNCA executar em producao.
# Uso: ./scripts/restore_to_test.sh [latest|YYYY-MM-DD_HHMMSS]
#
# Variaveis de ambiente obrigatorias:
#   DATABASE_URL    — URL do banco de teste (nunca producao)
#
# Variaveis de seguranca (TODAS obrigatorias):
#   SAFETY_BLOCKED_HOSTS — lista separada por virgula de
#                          hosts bloqueados (ex: db-prod-1,db-prod-2)
#   ALLOW_TEST_RESTORE   — deve ser exatamente "YES" para permitir restore
#
# Variaveis opcionais:
#   GDRIVE_REMOTE      (default: gdrive)
#   GDRIVE_PATH        (default: querolink-backups)
#   BACKUP_DIR         (default: /app/backups)
#   TEST_MEDIA_DIR     (default: /tmp/querolink_restore_media)
#   PROJECT_DIR        (default: resolvido do script)
#   DJANGO_SETTINGS_MODULE  (default: app.config.settings.test_fast)
#   RCLONE_BIN         (default: rclone)
#   PG_RESTORE_BIN     (default: pg_restore)
# ============================================================

GDRIVE_REMOTE="${GDRIVE_REMOTE:-gdrive}"
GDRIVE_PATH="${GDRIVE_PATH:-querolink-backups}"
BACKUP_DIR="${BACKUP_DIR:-/app/backups}"
TEST_MEDIA_DIR="${TEST_MEDIA_DIR:-/tmp/querolink_restore_media}"
RCLONE_BIN="${RCLONE_BIN:-rclone}"
PG_RESTORE_BIN="${PG_RESTORE_BIN:-pg_restore}"
DATE_FILTER="${1:-latest}"
PGPASSFILE=""

log() { echo "[$(date +%H:%M:%S)] $*"; }
die() { log "FATAL: $*"; exit 1; }

cleanup() {
    local rc=$?
    if [ -n "${PGPASSFILE:-}" ] && [ -f "$PGPASSFILE" ]; then
        rm -f "$PGPASSFILE" 2>/dev/null || true
    fi
    return $rc
}
trap cleanup EXIT

log "===== QUEROLINK RESTORE (TESTE) ====="
log "ATENCAO: Isso vai SOBRESCREVER o banco e midia de teste."

# ── Parse DATABASE_URL ──────────────────────────────────────
parse_db_url() {
    python3 -c "
import os, sys, urllib.parse
url = os.environ.get('DATABASE_URL', '')
if not url:
    sys.exit(1)
try:
    parsed = urllib.parse.urlparse(url)
    if not parsed.hostname:
        sys.exit(1)
    host = parsed.hostname
    port = str(parsed.port) if parsed.port else '5432'
    user = urllib.parse.unquote(parsed.username) if parsed.username else 'postgres'
    pwd  = urllib.parse.unquote(parsed.password) if parsed.password else ''
    db   = parsed.path.lstrip('/') or 'postgres'
    sys.stdout.write(host + '\n' + port + '\n' + user + '\n' + pwd + '\n' + db + '\n')
except Exception:
    sys.exit(1)
"
}

if [ -z "${DATABASE_URL:-}" ]; then
    die "DATABASE_URL nao configurada. Forneca a URL do banco de teste."
fi

DB_INFO=$(parse_db_url) || die "nao foi possivel parsear DATABASE_URL"

mapfile -t DB_PARTS <<< "$DB_INFO"
DB_HOST="${DB_PARTS[0]:-}"
DB_PORT="${DB_PARTS[1]:-}"
DB_USER="${DB_PARTS[2]:-}"
DB_PASS="${DB_PARTS[3]:-}"
DB_NAME="${DB_PARTS[4]:-}"

[ -n "$DB_HOST" ] || die "DB_HOST vazio apos parse de DATABASE_URL"
[ -n "$DB_PORT" ] || die "DB_PORT vazio apos parse de DATABASE_URL"
[ -n "$DB_USER" ] || die "DB_USER vazio apos parse de DATABASE_URL"
[ -n "$DB_NAME" ] || die "DB_NAME vazio apos parse de DATABASE_URL"

# ── Safety gate: bloquear hosts de producao ──────────────────
SAFETY_BLOCKED_HOSTS="${SAFETY_BLOCKED_HOSTS:-}"
if [ -n "$SAFETY_BLOCKED_HOSTS" ]; then
    IFS=',' read -ra BLOCKED <<< "$SAFETY_BLOCKED_HOSTS"
    for blocked_host in "${BLOCKED[@]}"; do
        blocked_host=$(echo "$blocked_host" | xargs)
        if [ "$DB_HOST" = "$blocked_host" ]; then
            die "Restore bloqueado: host '${DB_HOST}' esta na lista de bloqueio de seguranca (SAFETY_BLOCKED_HOSTS)"
        fi
    done
    log "Safety gate: host '${DB_HOST}' nao esta na lista de bloqueio. Prosseguindo."
fi

# ── Safety gate: ALLOW_TEST_RESTORE deve ser YES ────────────
if [ "${ALLOW_TEST_RESTORE:-}" != "YES" ]; then
    die "ALLOW_TEST_RESTORE nao esta configurado como YES. Restore bloqueado por seguranca."
fi

# ── Safety gate: nome do banco deve indicar teste ──────────
lower_db=$(echo "$DB_NAME" | tr '[:upper:]' '[:lower:]')
case "$lower_db" in
    *test*|*staging*|*qa*)
        log "Nome do banco '${DB_NAME}' parece ser de teste. Prosseguindo."
        ;;
    *)
        die "Nome do banco '${DB_NAME}' nao parece ser de teste (deve conter 'test', 'staging' ou 'qa')."
        ;;
esac

log "Banco de teste: ${DB_HOST}:${DB_PORT}/${DB_NAME}"

# ── Find backup on Google Drive ──────────────────────────────
log "Procurando backup '${DATE_FILTER}' no Google Drive ..."

find_remote_file() {
    local pattern="$1"
    local file
    if [ "$DATE_FILTER" = "latest" ]; then
        file=$("$RCLONE_BIN" ls "${GDRIVE_REMOTE}:${GDRIVE_PATH}" \
            --include "${pattern}" \
            | sort -k2 | tail -1 | awk '{print $2}')
    else
        file=$("$RCLONE_BIN" ls "${GDRIVE_REMOTE}:${GDRIVE_PATH}" \
            --include "querolink_${DATE_FILTER}*" \
            | sort -k2 | tail -1 | awk '{print $2}')
        if [ -z "$file" ]; then
            file=$("$RCLONE_BIN" ls "${GDRIVE_REMOTE}:${GDRIVE_PATH}" \
                --include "querolink_${DATE_FILTER}*" \
                | grep "${pattern}$" | tail -1 | awk '{print $2}')
        fi
    fi
    echo "$file"
}

DUMP_REMOTE=$(find_remote_file "querolink_*.dump")
if [ -z "$DUMP_REMOTE" ]; then
    die "Nenhum dump encontrado para: ${DATE_FILTER}"
fi

TIMESTAMP_PART=$(echo "$DUMP_REMOTE" | sed 's/^querolink_//; s/\.dump$//')
MEDIA_REMOTE="querolink_${TIMESTAMP_PART}.media.tar.gz"
MANIFEST_REMOTE="querolink_${TIMESTAMP_PART}.manifest.json"

log "Dump:      ${DUMP_REMOTE}"
log "Midia:     ${MEDIA_REMOTE}"
log "Manifesto: ${MANIFEST_REMOTE}"

mkdir -p "$BACKUP_DIR"
mkdir -p "$TEST_MEDIA_DIR"

# ── Download ─────────────────────────────────────────────────
log "Baixando dump ..."
"$RCLONE_BIN" copyto "${GDRIVE_REMOTE}:${GDRIVE_PATH}/${DUMP_REMOTE}" "${BACKUP_DIR}/${DUMP_REMOTE}"

log "Baixando midia ..."
"$RCLONE_BIN" copyto "${GDRIVE_REMOTE}:${GDRIVE_PATH}/${MEDIA_REMOTE}" "${BACKUP_DIR}/${MEDIA_REMOTE}"

log "Baixando manifesto ..."
"$RCLONE_BIN" copyto "${GDRIVE_REMOTE}:${GDRIVE_PATH}/${MANIFEST_REMOTE}" "${BACKUP_DIR}/${MANIFEST_REMOTE}"

DUMP_FILE="${BACKUP_DIR}/${DUMP_REMOTE}"
MEDIA_FILE="${BACKUP_DIR}/${MEDIA_REMOTE}"
MANIFEST_FILE="${BACKUP_DIR}/${MANIFEST_REMOTE}"

if [ ! -f "$DUMP_FILE" ] || [ ! -s "$DUMP_FILE" ]; then
    die "Dump baixado esta vazio ou ausente: ${DUMP_FILE}"
fi
if [ ! -f "$MEDIA_FILE" ] || [ ! -s "$MEDIA_FILE" ]; then
    die "Midia baixada esta vazia ou ausente: ${MEDIA_FILE}"
fi
if [ ! -f "$MANIFEST_FILE" ] || [ ! -s "$MANIFEST_FILE" ]; then
    die "Manifesto baixado esta vazio ou ausente: ${MANIFEST_FILE}"
fi

# ── Verify checksums ──────────────────────────────────────────
log "Verificando checksums contra o manifesto ..."

verify_checksum() {
    local file="$1"
    local expected_sha256="$2"
    local label="$3"
    local actual_sha256
    actual_sha256=$(sha256sum "$file" | cut -d' ' -f1)
    if [ "$actual_sha256" != "$expected_sha256" ]; then
        die "Checksum ${label} diverge: esperado=${expected_sha256}, obtido=${actual_sha256}"
    fi
    log "  OK: ${label} checksum confere."
}

MANIFEST_DUMP_SHA256=$(python3 -c "
import json, sys
with open('${MANIFEST_FILE}') as f:
    m = json.load(f)
sys.stdout.write(m['files']['dump']['sha256'])
")

MANIFEST_MEDIA_SHA256=$(python3 -c "
import json, sys
with open('${MANIFEST_FILE}') as f:
    m = json.load(f)
sys.stdout.write(m['files']['media']['sha256'])
")

verify_checksum "$DUMP_FILE" "$MANIFEST_DUMP_SHA256" "dump"
verify_checksum "$MEDIA_FILE" "$MANIFEST_MEDIA_SHA256" "midia"

# ── Interactive confirmation ──────────────────────────────────
log ""
log "### ATENCAO: Isso vai SOBRESCREVER ###"
log "  Banco:  ${DB_NAME}@${DB_HOST}:${DB_PORT}"
log "  Midia:  ${TEST_MEDIA_DIR}"
log "  Dump:   ${DUMP_REMOTE} ($(du -h "$DUMP_FILE" | cut -f1))"
log ""
echo -n "Confirmar restauracao em ambiente de teste? (SIM/NAO): "
read -r CONFIRM

if [ "$CONFIRM" != "SIM" ]; then
    log "Restauracao cancelada pelo usuario."
    exit 0
fi

# ── Restore dump ──────────────────────────────────────────────
log "Restaurando dump no banco de teste ..."
export PGPASSWORD="$DB_PASS"
"$PG_RESTORE_BIN" \
    -h "$DB_HOST" \
    -p "$DB_PORT" \
    -U "$DB_USER" \
    -d "$DB_NAME" \
    --clean \
    --if-exists \
    --no-owner \
    --no-acl \
    -j 2 \
    "$DUMP_FILE"
unset PGPASSWORD
log "Dump restaurado com sucesso."

# ── Restore media ─────────────────────────────────────────────
log "Restaurando midia em ${TEST_MEDIA_DIR} ..."
rm -rf "${TEST_MEDIA_DIR:?}"/*
tar -xzf "$MEDIA_FILE" -C "$TEST_MEDIA_DIR"
log "Midia restaurada com sucesso."
log "Arquivos em ${TEST_MEDIA_DIR}: $(find "$TEST_MEDIA_DIR" -type f | wc -l)"

# ── Django check ──────────────────────────────────────────────
log "Executando python manage.py check ..."

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-app.config.settings.test_fast}"

if command -v python &>/dev/null; then
    DJANGO_CHECK_CMD="python"
elif command -v python3 &>/dev/null; then
    DJANGO_CHECK_CMD="python3"
else
    log "AVISO: python nao encontrado. Pulando manage.py check."
    DJANGO_CHECK_CMD=""
fi

if [ -n "$DJANGO_CHECK_CMD" ]; then
    if cd "$PROJECT_DIR" 2>/dev/null; then
        if ! DJANGO_SETTINGS_MODULE="$DJANGO_SETTINGS_MODULE" "$DJANGO_CHECK_CMD" manage.py check 2>&1; then
            log "FALHA: manage.py check retornou erro. Restore considerado invalido."
            cd "$OLDPWD" 2>/dev/null || true
            die "manage.py check falhou - restore abortado."
        fi
        cd "$OLDPWD" 2>/dev/null || true
    else
        die "Nao foi possivel acessar PROJECT_DIR=${PROJECT_DIR}. Restore abortado."
    fi
fi

log "===== RESTAURACAO CONCLUIDA ====="
log "Banco de teste:  ${DB_NAME}@${DB_HOST}:${DB_PORT}"
log "Midia de teste:  ${TEST_MEDIA_DIR}"
log "Manifesto usado: ${MANIFEST_REMOTE}"
