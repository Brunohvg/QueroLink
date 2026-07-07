#!/bin/bash
set -e

# ============================================================
# V-Com entrypoint — deploy automático
# ============================================================

log() { echo "[$(date +%H:%M:%S)] $*"; }

# ── Validar variáveis obrigatórias ─────────────────────────
[ -z "$SECRET_KEY" ] && log "FATAL: SECRET_KEY nao configurada." && exit 1
[ -z "$DATABASE_URL" ] && log "FATAL: DATABASE_URL nao configurada." && exit 1
[ -z "$FERNET_KEY" ] && log "FATAL: FERNET_KEY nao configurada. Gere com: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"" && exit 1
[ -z "$JWT_SIGNING_KEY" ] && log "WARNING: JWT_SIGNING_KEY nao configurada. Usando SECRET_KEY como fallback."

# ── Esperar banco ──────────────────────────────────────────
wait_for_db() {
    log "Aguardando banco de dados..."
    local retries=30
    local last_error=""
    while [ $retries -gt 0 ]; do
        last_error=$(python -c "
import urllib.parse, os, psycopg2
url = os.environ['DATABASE_URL']
parsed = urllib.parse.urlparse(url)
conn = psycopg2.connect(
    host=parsed.hostname or 'localhost',
    port=parsed.port or 5432,
    user=parsed.username or 'postgres',
    password=parsed.password or '',
    dbname=parsed.path.lstrip('/') or 'postgres',
)
conn.close()
print('OK')
" 2>&1)
        if [ "$last_error" = "OK" ]; then
            log "Banco disponível."
            return 0
        fi
        retries=$((retries - 1))
        sleep 2
    done
    log "ERRO: Banco não respondeu após 60s. Último erro capturado:"
    log "$last_error"
    return 1
}

# ── Apenas o container principal ───────────────────────────
if [ -z "$1" ] || [ "$1" = 'gunicorn' ]; then

    # ── Checar banco ───────────────────────────────────────
    if [ -n "$DATABASE_URL" ]; then
        if ! wait_for_db; then
            log "FATAL: não foi possível conectar ao banco. Abortando boot."
            exit 1
        fi
    fi

    # ── Migrations ─────────────────────────────────────────
    log "===== MIGRATIONS ====="
    python manage.py migrate --noinput
    log "Migrations concluídas."

    # ── Seed (opcional, só se SEED_ON_START=true) ──────────
    if [ "${SEED_ON_START:-false}" = "true" ]; then
        log "===== SEED ====="
        python -c "
import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','app.config.settings.production')
import django; django.setup()
exec(open('seed.py').read())
"
        log "Seed concluído."
    fi

    # ── Static files ───────────────────────────────────────
    log "===== COLLECTSTATIC ====="
    python manage.py collectstatic --noinput
    log "Arquivos estáticos OK."

    # ── Gunicorn ───────────────────────────────────────────
    log "===== GUNICORN ====="
    exec gunicorn app.config.wsgi:application \
        --bind 0.0.0.0:8000 \
        --workers ${GUNICORN_WORKERS:-3} \
        --timeout ${GUNICORN_TIMEOUT:-120} \
        --max-requests 1000 \
        --max-requests-jitter 200 \
        --preload \
        --graceful-timeout 60 \
        --access-logfile - \
        --error-logfile -

else
    # ── Serviços auxiliares (celery worker, celery beat) ──
    log "Iniciando serviço: $*"

    # ── Esperar banco para serviços Celery ─────────────────
    if [ -n "$DATABASE_URL" ]; then
        if ! wait_for_db; then
            log "FATAL: não foi possível conectar ao banco. Abortando boot."
            exit 1
        fi
    fi

    exec "$@"
fi
