#!/bin/bash
set -Eeuo pipefail

# ============================================================
# QueroLink — backup.sh
# Faz dump do PostgreSQL e envia para Google Drive via rclone
# Uso: ./scripts/backup.sh
#
# Teste manual dentro do container celery_worker:
#   rclone version
#   rclone listremotes
#   rclone lsd gdrive:
#   /app/scripts/backup.sh
#   rclone lsf "gdrive:${GDRIVE_PATH:-querolink-backups}" --include "querolink_*.dump"
#
# Teste de restauracao (em banco PostgreSQL VAZIO, nunca producao):
#   pg_restore -h HOST -p PORT -U USER -d DB --clean --if-exists --no-owner --no-acl arquivo.dump
# ============================================================

BACKUP_DIR="${BACKUP_DIR:-/app/backups}"
GDRIVE_REMOTE="${GDRIVE_REMOTE:-gdrive}"
GDRIVE_PATH="${GDRIVE_PATH:-querolink-backups}"
LOCAL_RETENTION_DAYS="${LOCAL_RETENTION_DAYS:-2}"
REMOTE_RETENTION_DAYS="${REMOTE_RETENTION_DAYS:-30}"
TIMESTAMP=$(date +%Y-%m-%d_%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/querolink_${TIMESTAMP}.dump"
BACKUP_FILENAME="querolink_${TIMESTAMP}.dump"
PGPASSFILE=""

log() { echo "[$(date +%H:%M:%S)] $*"; }
die() { log "FATAL: $*"; exit 1; }

cleanup() {
    local rc=$?
    if [ -n "${PGPASSFILE:-}" ] && [ -f "$PGPASSFILE" ]; then
        rm -f "$PGPASSFILE" 2>/dev/null || true
    fi
    if [ $rc -ne 0 ] && [ -n "${BACKUP_FILE:-}" ] && [ -f "$BACKUP_FILE" ]; then
        rm -f "$BACKUP_FILE" 2>/dev/null || true
    fi
    return $rc
}
trap cleanup EXIT

log "===== QUEROLINK BACKUP ====="

# ── Validate DATABASE_URL ───────────────────────────────────
if [ -z "${DATABASE_URL:-}" ]; then
    die "DATABASE_URL nao configurada"
fi

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

log "Conectando em ${DB_HOST}:${DB_PORT}/${DB_NAME} como ${DB_USER}"

mkdir -p "$BACKUP_DIR"

# ── Validate rclone / Google Drive before expensive dump ────
RCLONE_BIN="${RCLONE_BIN:-rclone}"

if ! command -v "$RCLONE_BIN" &>/dev/null; then
    die "rclone nao encontrado"
fi

if ! "$RCLONE_BIN" listremotes 2>/dev/null | grep -q "^${GDRIVE_REMOTE}:"; then
    die "Remote rclone '${GDRIVE_REMOTE}' nao configurado"
fi

log "Validando acesso ao Google Drive em ${GDRIVE_REMOTE}:${GDRIVE_PATH} ..."
if ! "$RCLONE_BIN" mkdir "${GDRIVE_REMOTE}:${GDRIVE_PATH}" >/dev/null 2>&1; then
    die "Nao foi possivel acessar/criar pasta no Google Drive. Verifique token, refresh_token, escopo e permissoes do remote '${GDRIVE_REMOTE}'."
fi

# ── pg_dump ─────────────────────────────────────────────────
log "Executando pg_dump ..."
PGPASSFILE=$(mktemp)
chmod 600 "$PGPASSFILE"
printf '%s:%s:%s:%s:%s\n' "$DB_HOST" "$DB_PORT" "$DB_NAME" "$DB_USER" "$DB_PASS" > "$PGPASSFILE"
export PGPASSFILE="$PGPASSFILE"
unset PGPASSWORD

# Allow overriding pg_dump binary for testing
PG_DUMP_BIN="${PG_DUMP_BIN:-pg_dump}"

"$PG_DUMP_BIN" \
    -h "$DB_HOST" \
    -p "$DB_PORT" \
    -U "$DB_USER" \
    -d "$DB_NAME" \
    -Fc \
    -Z9 \
    -f "$BACKUP_FILE"

rm -f "$PGPASSFILE"
PGPASSFILE=""

if [ ! -f "$BACKUP_FILE" ]; then
    die "pg_dump nao criou o arquivo: $BACKUP_FILE"
fi

SIZE=$(du -h "$BACKUP_FILE" | cut -f1)

if [ ! -s "$BACKUP_FILE" ]; then
    die "Arquivo de backup vazio: $BACKUP_FILE"
fi

log "Dump criado: ${BACKUP_FILENAME} ($SIZE)"

# ── Upload to Google Drive ──────────────────────────────────
log "Enviando para ${GDRIVE_REMOTE}:${GDRIVE_PATH}/${BACKUP_FILENAME} ..."

"$RCLONE_BIN" copyto "$BACKUP_FILE" "${GDRIVE_REMOTE}:${GDRIVE_PATH}/${BACKUP_FILENAME}"

# Confirm remote file exists
log "Confirmando arquivo no remoto ..."
REMOTE_CHECK=$("$RCLONE_BIN" lsf "${GDRIVE_REMOTE}:${GDRIVE_PATH}" --include "${BACKUP_FILENAME}" 2>&1)
if [ -z "$REMOTE_CHECK" ]; then
    die "Arquivo NAO encontrado no Google Drive apos upload: ${BACKUP_FILENAME}"
fi

log "Upload concluido e verificado."

# ── Cleanup remote backups older than N days ────────────────
log "Limpando backups remotos > ${REMOTE_RETENTION_DAYS} dias ..."

DELETED_OUTPUT=$("$RCLONE_BIN" delete "${GDRIVE_REMOTE}:${GDRIVE_PATH}" \
    --min-age "${REMOTE_RETENTION_DAYS}d" \
    --include "querolink_*.dump" \
    --dry-run 2>&1) || true

if echo "$DELETED_OUTPUT" | grep -q "querolink_"; then
    "$RCLONE_BIN" delete "${GDRIVE_REMOTE}:${GDRIVE_PATH}" \
        --min-age "${REMOTE_RETENTION_DAYS}d" \
        --include "querolink_*.dump"
    DELETED_COUNT=$(echo "$DELETED_OUTPUT" | grep -c "querolink_" || echo "0")
    log "Removidos ${DELETED_COUNT} backups antigos do Drive."
else
    log "Nenhum backup antigo para remover."
fi

# ── Cleanup local backups ───────────────────────────────────
log "Limpando backups locais > ${LOCAL_RETENTION_DAYS} dias ..."
find "$BACKUP_DIR" -name "querolink_*.dump" -mtime "+${LOCAL_RETENTION_DAYS}" -delete 2>/dev/null || true

log "===== BACKUP CONCLUIDO ====="
