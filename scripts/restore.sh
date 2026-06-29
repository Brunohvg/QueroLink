#!/bin/bash
set -e

# ============================================================
# QueroLink — restore.sh
# Baixa e restaura um backup do Google Drive
# Uso: ./scripts/restore.sh <data>   (ex: 2026-06-29)
#      ./scripts/restore.sh latest   (baixa o mais recente)
# ============================================================

GDRIVE_REMOTE="${GDRIVE_REMOTE:-gdrive}"
GDRIVE_PATH="${GDRIVE_PATH:-querolink-backups}"
BACKUP_DIR="${BACKUP_DIR:-/app/backups}"

log() { echo "[$(date +%H:%M:%S)] $*"; }

DATE_FILTER="${1:-latest}"

if [ "$DATE_FILTER" = "latest" ]; then
    log "Procurando backup mais recente no Google Drive ..."
    FILE=$(rclone ls "${GDRIVE_REMOTE}:${GDRIVE_PATH}" \
        --include "querolink_*.dump.gz" \
        | sort -k2 | tail -1 | awk '{print $2}')
else
    log "Procurando backup de ${DATE_FILTER} ..."
    FILE=$(rclone ls "${GDRIVE_REMOTE}:${GDRIVE_PATH}" \
        --include "querolink_${DATE_FILTER}*.dump.gz" \
        | sort -k2 | tail -1 | awk '{print $2}')
fi

if [ -z "$FILE" ]; then
    log "FATAL: Nenhum backup encontrado para: ${DATE_FILTER}"
    exit 1
fi

log "Baixando: ${FILE} ..."
rclone copyto "${GDRIVE_REMOTE}:${GDRIVE_PATH}/${FILE}" "${BACKUP_DIR}/${FILE}"

parse_db_url() {
    python3 -c "
import os, urllib.parse
url = os.environ.get('DATABASE_URL', '')
parsed = urllib.parse.urlparse(url)
print(parsed.hostname or 'localhost')
print(parsed.port or 5432)
print(parsed.username or 'postgres')
print(parsed.password or '')
print(parsed.path.lstrip('/') or 'postgres')
"
}

read -r DB_HOST DB_PORT DB_USER DB_PASS DB_NAME <<< "$(parse_db_url)"

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

log "===== RESTAURACAO CONCLUIDA ====="
