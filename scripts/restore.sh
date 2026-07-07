#!/bin/bash
set -Eeuo pipefail

# ============================================================
# QueroLink — restore.sh
# Baixa e restaura um backup do Google Drive
# Uso: ./scripts/restore.sh <data>   (ex: 2026-06-29)
#      ./scripts/restore.sh latest   (baixa o mais recente)
#
# Importante: restaurar apenas em banco PostgreSQL VAZIO.
# Nunca executar sobre banco de producao em uso.
# ============================================================

GDRIVE_REMOTE="${GDRIVE_REMOTE:-gdrive}"
GDRIVE_PATH="${GDRIVE_PATH:-querolink-backups}"
BACKUP_DIR="${BACKUP_DIR:-/app/backups}"

log() { echo "[$(date +%H:%M:%S)] $*"; }
die() { log "FATAL: $*"; exit 1; }

RCLONE_BIN="${RCLONE_BIN:-rclone}"
DATE_FILTER="${1:-latest}"

if [ "$DATE_FILTER" = "latest" ]; then
    log "Procurando backup mais recente no Google Drive ..."
    FILE=$("$RCLONE_BIN" ls "${GDRIVE_REMOTE}:${GDRIVE_PATH}" \
        --include "querolink_*.dump" \
        | sort -k2 | tail -1 | awk '{print $2}')
else
    log "Procurando backup de ${DATE_FILTER} ..."
    FILE=$("$RCLONE_BIN" ls "${GDRIVE_REMOTE}:${GDRIVE_PATH}" \
        --include "querolink_${DATE_FILTER}*.dump" \
        | sort -k2 | tail -1 | awk '{print $2}')
fi

if [ -z "$FILE" ]; then
    die "Nenhum backup encontrado para: ${DATE_FILTER}"
fi

log "Baixando: ${FILE} ..."
"$RCLONE_BIN" copyto "${GDRIVE_REMOTE}:${GDRIVE_PATH}/${FILE}" "${BACKUP_DIR}/${FILE}"

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

log "ATENCAO: Isso vai SOBRESCREVER o banco '${DB_NAME}@${DB_HOST}'."
log "Arquivo: ${BACKUP_DIR}/${FILE}"
echo -n "Confirmar? (SIM/NAO): "
read -r CONFIRM

if [ "$CONFIRM" != "SIM" ]; then
    log "Restauracao cancelada."
    exit 0
fi

log "Restaurando ${FILE} ..."
export PGPASSWORD="$DB_PASS"
pg_restore \
    -h "$DB_HOST" \
    -p "$DB_PORT" \
    -U "$DB_USER" \
    -d "$DB_NAME" \
    --clean \
    --if-exists \
    --no-owner \
    --no-acl \
    -j 2 \
    "${BACKUP_DIR}/${FILE}"

unset PGPASSWORD

log "===== RESTAURACAO CONCLUIDA ====="
