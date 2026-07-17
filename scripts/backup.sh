#!/bin/bash
set -Eeuo pipefail

# ============================================================
# QueroLink — backup.sh
# Faz dump do PostgreSQL + mídia e envia para Google Drive via rclone
# Uso: ./scripts/backup.sh
#
# Teste manual dentro do container celery_worker:
#   rclone version
#   rclone listremotes
#   rclone lsd gdrive:
#   /app/scripts/backup.sh
#   rclone lsf "gdrive:${GDRIVE_PATH:-querolink-backups}" --include "querolink_*"
#
# Teste de restauracao em ambiente de teste:
#   scripts/restore_to_test.sh
# ============================================================

BACKUP_DIR="${BACKUP_DIR:-/app/backups}"
MEDIA_ROOT="${MEDIA_ROOT:-/app/media}"
GDRIVE_REMOTE="${GDRIVE_REMOTE:-gdrive}"
GDRIVE_PATH="${GDRIVE_PATH:-querolink-backups}"
LOCAL_RETENTION_DAYS="${LOCAL_RETENTION_DAYS:-2}"
REMOTE_RETENTION_DAYS="${REMOTE_RETENTION_DAYS:-30}"
TIMESTAMP=$(date +%Y-%m-%d_%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/querolink_${TIMESTAMP}.dump"
BACKUP_FILENAME="querolink_${TIMESTAMP}.dump"
MEDIA_FILE="${BACKUP_DIR}/querolink_${TIMESTAMP}.media.tar.gz"
MEDIA_FILENAME="querolink_${TIMESTAMP}.media.tar.gz"
MANIFEST_FILE="${BACKUP_DIR}/querolink_${TIMESTAMP}.manifest.json"
MANIFEST_FILENAME="querolink_${TIMESTAMP}.manifest.json"
PGPASSFILE=""

log() { echo "[$(date +%H:%M:%S)] $*"; }
die() { log "FATAL: $*"; exit 1; }

cleanup() {
    local rc=$?
    if [ -n "${PGPASSFILE:-}" ] && [ -f "$PGPASSFILE" ]; then
        rm -f "$PGPASSFILE" 2>/dev/null || true
    fi
    if [ $rc -ne 0 ]; then
        for f in "$BACKUP_FILE" "$MEDIA_FILE" "$MANIFEST_FILE"; do
            [ -n "${f:-}" ] && [ -f "$f" ] && rm -f "$f" 2>/dev/null || true
        done
    fi
    return $rc
}
trap cleanup EXIT

log "===== QUEROLINK BACKUP ====="

# ── Validate DATABASE_URL ───────────────────────────────────
if [ -z "${DATABASE_URL:-}" ]; then
    die "DATABASE_URL nao configurada"
fi

# ── Validate MEDIA_ROOT ─────────────────────────────────────
if [ ! -d "$MEDIA_ROOT" ]; then
    die "MEDIA_ROOT nao encontrado: ${MEDIA_ROOT}"
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

if [ ! -s "$BACKUP_FILE" ]; then
    die "Arquivo de backup vazio: $BACKUP_FILE"
fi

DUMP_SIZE=$(stat --printf="%s" "$BACKUP_FILE" 2>/dev/null || stat -f%z "$BACKUP_FILE" 2>/dev/null || echo "0")
log "Dump criado: ${BACKUP_FILENAME} ($(numfmt --to=iec "$DUMP_SIZE" 2>/dev/null || echo "${DUMP_SIZE} bytes"))"

# ── Media archive ────────────────────────────────────────────
log "Compactando MEDIA_ROOT (${MEDIA_ROOT}) ..."
tar -czf "$MEDIA_FILE" -C "$MEDIA_ROOT" .

if [ ! -f "$MEDIA_FILE" ]; then
    die "Arquivo de midia nao foi criado: $MEDIA_FILE"
fi

MEDIA_SIZE=$(stat --printf="%s" "$MEDIA_FILE" 2>/dev/null || stat -f%z "$MEDIA_FILE" 2>/dev/null || echo "0")
log "Midia criada: ${MEDIA_FILENAME} ($(numfmt --to=iec "$MEDIA_SIZE" 2>/dev/null || echo "${MEDIA_SIZE} bytes"))"

# ── SHA-256 checksums ────────────────────────────────────────
log "Calculando checksums SHA-256 ..."
DUMP_SHA256=$(sha256sum "$BACKUP_FILE" | cut -d' ' -f1)
MEDIA_SHA256=$(sha256sum "$MEDIA_FILE" | cut -d' ' -f1)
log "Dump SHA256:   ${DUMP_SHA256}"
log "Midia SHA256:  ${MEDIA_SHA256}"

# ── Manifest ──────────────────────────────────────────────────
log "Criando manifesto ..."
cat > "$MANIFEST_FILE" <<MANIFEST_EOF
{
  "timestamp": "${TIMESTAMP}",
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "database": "${DB_NAME}@${DB_HOST}:${DB_PORT}",
  "files": {
    "dump": {
      "filename": "${BACKUP_FILENAME}",
      "size_bytes": ${DUMP_SIZE},
      "sha256": "${DUMP_SHA256}"
    },
    "media": {
      "filename": "${MEDIA_FILENAME}",
      "size_bytes": ${MEDIA_SIZE},
      "sha256": "${MEDIA_SHA256}"
    }
  }
}
MANIFEST_EOF

if [ ! -f "$MANIFEST_FILE" ]; then
    die "Manifesto nao foi criado: $MANIFEST_FILE"
fi

log "Manifesto criado: ${MANIFEST_FILENAME}"

# ── Upload to Google Drive ──────────────────────────────────
log "Enviando dump para ${GDRIVE_REMOTE}:${GDRIVE_PATH}/${BACKUP_FILENAME} ..."
"$RCLONE_BIN" copyto "$BACKUP_FILE" "${GDRIVE_REMOTE}:${GDRIVE_PATH}/${BACKUP_FILENAME}"

log "Enviando midia para ${GDRIVE_REMOTE}:${GDRIVE_PATH}/${MEDIA_FILENAME} ..."
"$RCLONE_BIN" copyto "$MEDIA_FILE" "${GDRIVE_REMOTE}:${GDRIVE_PATH}/${MEDIA_FILENAME}"

log "Enviando manifesto para ${GDRIVE_REMOTE}:${GDRIVE_PATH}/${MANIFEST_FILENAME} ..."
"$RCLONE_BIN" copyto "$MANIFEST_FILE" "${GDRIVE_REMOTE}:${GDRIVE_PATH}/${MANIFEST_FILENAME}"

# ── Confirm remote files exist ───────────────────────────────
log "Confirmando arquivos no remoto ..."

confirm_remote() {
    local filename="$1"
    local label="$2"
    local check
    check=$("$RCLONE_BIN" lsf "${GDRIVE_REMOTE}:${GDRIVE_PATH}" --include "${filename}" 2>&1)
    if [ -z "$check" ]; then
        die "Arquivo NAO encontrado no Google Drive apos upload: ${filename} (${label})"
    fi
    log "  OK: ${filename}"
}
confirm_remote "$BACKUP_FILENAME" "dump"
confirm_remote "$MEDIA_FILENAME" "midia"
confirm_remote "$MANIFEST_FILENAME" "manifesto"

log "Upload e verificacao concluidos para os 3 objetos."

# ── Cleanup remote backups older than N days ────────────────
log "Limpando backups remotos > ${REMOTE_RETENTION_DAYS} dias ..."

cleanup_remote_pattern() {
    local pattern="$1"
    local label="$2"
    local dry_output
    dry_output=$("$RCLONE_BIN" delete "${GDRIVE_REMOTE}:${GDRIVE_PATH}" \
        --min-age "${REMOTE_RETENTION_DAYS}d" \
        --include "${pattern}" \
        --dry-run 2>&1) || true
    if echo "$dry_output" | grep -qE "querolink_"; then
        "$RCLONE_BIN" delete "${GDRIVE_REMOTE}:${GDRIVE_PATH}" \
            --min-age "${REMOTE_RETENTION_DAYS}d" \
            --include "${pattern}"
        local deleted_count
        deleted_count=$(echo "$dry_output" | grep -cE "querolink_" || echo "0")
        log "  Removidos ${deleted_count} ${label} antigos do Drive."
    fi
}

cleanup_remote_pattern "querolink_*.dump" "dumps"
cleanup_remote_pattern "querolink_*.media.tar.gz" "arquivos de midia"
cleanup_remote_pattern "querolink_*.manifest.json" "manifestos"

# ── Cleanup local backups ───────────────────────────────────
log "Limpando backups locais > ${LOCAL_RETENTION_DAYS} dias ..."
find "$BACKUP_DIR" -name "querolink_*.dump" -mtime "+${LOCAL_RETENTION_DAYS}" -delete 2>/dev/null || true
find "$BACKUP_DIR" -name "querolink_*.media.tar.gz" -mtime "+${LOCAL_RETENTION_DAYS}" -delete 2>/dev/null || true
find "$BACKUP_DIR" -name "querolink_*.manifest.json" -mtime "+${LOCAL_RETENTION_DAYS}" -delete 2>/dev/null || true

log "===== BACKUP CONCLUIDO ====="
