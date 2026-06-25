#!/bin/bash
set -e

# ============================================================
# QueroLink — Deploy Script
# Uso: scripts/deploy.sh [staging|production]
# ============================================================

ENV="${1:-staging}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

log() { echo "[$(date +%H:%M:%S)] $*"; }
error() { echo "[$(date +%H:%M:%S)] ERRO: $*" >&2; exit 1; }

log "===== DEPLOY: $ENV ====="

# ── 1. Verificar pré-requisitos ────────────────────────────
command -v docker >/dev/null 2>&1 || error "Docker não encontrado. Instale primeiro."
command -v docker compose >/dev/null 2>&1 || command -v docker-compose >/dev/null 2>&1 || error "Docker Compose não encontrado."
COMPOSE="docker compose"
docker compose version >/dev/null 2>&1 || COMPOSE="docker-compose"

# ── 2. Verificar .env ──────────────────────────────────────
if [ ! -f ".env" ]; then
    error "Arquivo .env não encontrado. Crie a partir de .env.example"
fi

# ── 3. Validar variáveis críticas ──────────────────────────
source .env
[ -z "$SECRET_KEY" ] && error "SECRET_KEY não configurada no .env"

if [ "$ENV" = "production" ]; then
    [ -z "$DATABASE_URL" ] && error "DATABASE_URL é obrigatória em produção."
    export SEED_ON_START=false
elif [ "$ENV" = "staging" ]; then
    [ -z "$DATABASE_URL" ] && error "DATABASE_URL é obrigatória em staging."
    export SEED_ON_START="${SEED_ON_START:-true}"
fi

# ── 4. Backup (produção) ───────────────────────────────────
if [ "$ENV" = "production" ] && [ -n "$DATABASE_URL" ]; then
    log "===== BACKUP (produção) ====="
    mkdir -p backups
    BACKUP_FILE="backup_$(date +%Y%m%d_%H%M%S).sql"
    if python -c "
import os, subprocess
url = os.environ.get('DATABASE_URL', '')
if url and 'postgres' in url:
    subprocess.run(['pg_dump', os.environ['DATABASE_URL'], '-f', 'backups/${BACKUP_FILE}'], check=True)
    print('Backup salvo em backups/${BACKUP_FILE}')
"; then
        log "Backup salvo em backups/$BACKUP_FILE"
        find backups -name 'backup_*.sql' -type f -mtime +30 -delete 2>/dev/null || true
    else
        error "Backup automático falhou. Corrija o erro antes de continuar."
    fi
fi

# ── 5. Build + Deploy ──────────────────────────────────────
log "===== BUILD ====="
$COMPOSE build --no-cache

log "===== UP ====="
$COMPOSE down --remove-orphans 2>/dev/null || true
$COMPOSE up -d

log "===== AGUARDANDO SAÚDE ====="
RETRIES=30
while [ $RETRIES -gt 0 ]; do
    if curl -sf http://localhost:8000/health/ >/dev/null 2>&1; then
        log "Aplicação saudável em http://localhost:8000/health/"
        break
    fi
    RETRIES=$((RETRIES - 1))
    sleep 2
    log "Aguardando... ($RETRIES tentativas restantes)"
done

if [ $RETRIES -eq 0 ]; then
    error "Aplicação não respondeu após 60s. Verifique os logs: $COMPOSE logs web"
fi

# ── 6. Logs iniciais ───────────────────────────────────────
log "===== LOGS RECENTES ====="
$COMPOSE logs --tail=30 web

log "===== DEPLOY $ENV CONCLUÍDO ====="
echo ""
echo "URLs principais:"
echo "  API Swagger:  /api/schema/swagger-ui/"
echo "  Admin Django: /admin/"
echo "  Dashboard:    /dashboard/"
echo "  Mobile login: /dashboard/mobile/login/"
echo ""
echo "Para logs contínuos: $COMPOSE logs -f"
