#!/bin/bash
set -e

# ============================================================
# QueroLink — backup.sh
# Faz dump do PostgreSQL e envia para Google Drive via rclone
# Uso: ./scripts/backup.sh
# ============================================================

BACKUP_DIR="${BACKUP_DIR:-/app/backups}"
GDRIVE_REMOTE="${GDRIVE_REMOTE:-gdrive}"
GDRIVE_PATH="${GDRIVE_PATH:-querolink-backups}"
LOCAL_RETENTION_DAYS="${LOCAL_RETENTION_DAYS:-2}"
REMOTE_RETENTION_DAYS="${REMOTE_RETENTION_DAYS:-30}"
TIMESTAMP=$(date +%Y-%m-%d_%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/querolink_${TIMESTAMP}.dump.gz"

log() { echo "[$(date +%H:%M:%S)] $*"; }

# ── Parse DATABASE_URL ──────────────────────────────────────
parse_db_url() {
    python3 -c "
import os, urllib.parse
url = os.environ.get('DATABASE_URL', '')
if not url:
    print('ERROR: DATABASE_URL not set')
    exit(1)
try:
    parsed = urllib.parse.urlparse(url)
    print(parsed.hostname or 'localhost')
    print(parsed.port or 5432)
    print(parsed.username or 'postgres')
    print(parsed.password or '')
    print(parsed.path.lstrip('/') or 'postgres')
except Exception as e:
    print(f'ERROR: {e}')
    exit(1)
"
}

log "===== QUEROLINK BACKUP ====="

# ── Database connection ─────────────────────────────────────
read -r DB_HOST DB_PORT DB_USER DB_PASS DB_NAME <<< "$(parse_db_url)"

if [ -z "$DB_HOST" ]; then
    log "FATAL: nao foi possivel parsear DATABASE_URL"
    exit 1
fi

mkdir -p "$BACKUP_DIR"

# ── pg_dump ─────────────────────────────────────────────────
log "Executando pg_dump de ${DB_NAME}@${DB_HOST}:${DB_PORT} ..."
export PGPASSWORD="$DB_PASS"
pg_dump \
    -h "$DB_HOST" \
    -p "$DB_PORT" \
    -U "$DB_USER" \
    -d "$DB_NAME" \
    -Fc \
    -Z9 \
    -f "$BACKUP_FILE"

SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
log "Dump criado: $BACKUP_FILE ($SIZE)"

# ── Upload to Google Drive ──────────────────────────────────
log "Enviando para Google Drive (${GDRIVE_REMOTE}:${GDRIVE_PATH}) ..."
rclone copy "$BACKUP_DIR" "${GDRIVE_REMOTE}:${GDRIVE_PATH}" \
    --include "querolink_*.dump.gz" \
    --no-traverse \
    2>&1 | tail -1

# ── Cleanup remote backups older than N days ────────────────
log "Limpando backups remotos > ${REMOTE_RETENTION_DAYS} dias ..."
DELETED=$(rclone delete "${GDRIVE_REMOTE}:${GDRIVE_PATH}" \
    --min-age "${REMOTE_RETENTION_DAYS}d" \
    --include "querolink_*.dump.gz" \
    --dry-run 2>&1 | grep -c "querolink_")
if [ "$DELETED" -gt 0 ]; then
    rclone delete "${GDRIVE_REMOTE}:${GDRIVE_PATH}" \
        --min-age "${REMOTE_RETENTION_DAYS}d" \
        --include "querolink_*.dump.gz"
    log "Removidos ${DELETED} backups antigos do Drive."
else
    log "Nenhum backup antigo para remover."
fi

# ── Cleanup local backups ───────────────────────────────────
log "Limpando backups locais > ${LOCAL_RETENTION_DAYS} dias ..."
find "$BACKUP_DIR" -name "querolink_*.dump.gz" -mtime "+${LOCAL_RETENTION_DAYS}" -delete 2>/dev/null || true

log "===== BACKUP CONCLUIDO: ${BACKUP_FILE} (${SIZE}) ====="
