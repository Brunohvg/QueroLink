#!/bin/bash
set -e

# ============================================================
# QueroLink entrypoint — deploy automático
# ============================================================

log() { echo "[$(date +%H:%M:%S)] $*"; }

# ── Esperar banco ──────────────────────────────────────────
wait_for_db() {
    log "Aguardando banco de dados..."
    local retries=30
    local last_error=""
    while [ $retries -gt 0 ]; do
        last_error=$(python -c "
import dj_database_url, os, psycopg2
url = dj_database_url.parse(os.environ['DATABASE_URL'])
conn = psycopg2.connect(**{k:v for k,v in url.items() if k in ('host','port','user','password','dbname')})
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

    # ── Templates padrão (força criação) ───────────────────
    log "===== TEMPLATES ====="
    if ! python manage.py shell -c "
from app.apps.accounts.models import Tenant
from app.apps.notifications.models import MessageTemplate
DEFAULT = [
    ('seller_credentials','whatsapp','Ola {{vendedor}}! Seu acesso ao sistema de comissoes foi criado.\nUsuario: {{usuario}}\nSenha temporaria: {{senha}}\nAcesse e troque sua senha no primeiro login.'),
    ('commission_paid','whatsapp','Ola {{vendedor}}! Sua comissao de {{periodo}} no valor de {{valor}} foi paga. Confira os detalhes no app.'),
]
for t in Tenant.objects.all():
    for et, ch, body in DEFAULT:
        MessageTemplate.objects.get_or_create(tenant=t, event_type=et, channel=ch, defaults={'body':body})
print('Templates verificados.')
"; then
        log "AVISO: falha ao criar templates padrão — verificar manualmente. Continuando o boot."
    fi

    # ── Static files ───────────────────────────────────────
    log "===== COLLECTSTATIC ====="
    python manage.py collectstatic --noinput
    log "Arquivos estáticos coletados."

    # ── Gunicorn ───────────────────────────────────────────
    log "===== GUNICORN ====="
    exec gunicorn app.config.wsgi:application \
        --bind 0.0.0.0:8000 \
        --workers ${GUNICORN_WORKERS:-3} \
        --timeout ${GUNICORN_TIMEOUT:-120} \
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
