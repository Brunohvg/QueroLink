# QueroLink — Sistema de Comissões Multi-Tenant

Sistema de gestão de comissões para vendedores de lojas. Tenant principal: Loja Bibelô (16 vendedores).

---

## Stack

| Camada | Tecnologia |
|--------|------------|
| Backend | Python 3.12, Django 5.1, DRF |
| Async | Celery 5.4 + Redis 7 |
| Auth | JWT (simplejwt) + Token + Session |
| Frontend | Alpine.js + Tailwind CSS + Chart.js |
| PWA | Service Worker + Web Manifest (8 ícones) |
| Banco | SQLite (dev) / PostgreSQL (prod) |
| Deploy | Docker multi-stage, Gunicorn, Whitenoise |
| APIs externas | Pagar.me v5, Evolution API (WhatsApp) |
| Docs | Swagger UI (drf-spectacular) |

---

## Início rápido

```bash
# 1. Setup
python3 -m venv .venv && source .venv/bin/activate
uv pip install -r requirements/base.txt
cp .env.example .env

# 2. Banco + seed (dados de demonstração)
make migrate
make seed

# 3. Rodar
make dev
```

Acesse: `http://localhost:8000/dashboard/mobile/login/` (vendedor) ou `/dashboard/` (gestor).

Usuários de demo criados pelo `seed`:
- **Gestor**: `admin@bibelo.com.br` / `admin123`
- **Financeiro**: `financeiro` / `fin123`
- **Vendedores**: `bibelo`, `celia`, `danubia`, ... (ver `seed.py`)

---

## Comandos rápidos (Makefile)

```bash
make help          # Todos os comandos
make dev           # Servidor dev (porta 8000)
make test          # 55 testes
make seed          # Popular banco com dados demo
make reset-db      # Recriar banco do zero
make build         # Build Docker
make up            # Iniciar containers
make down          # Parar containers
make logs          # Logs Docker
make deploy-check  # Testes + verificação pré-deploy
make clean         # Limpar pycache
```

---

## Deploy

### Pré-requisitos

- Docker + Docker Compose
- `.env` configurado (copiar de `.env.example`)
- Rede Coolify existente (se usar Coolify): `docker network create coolify`

### Comando único

```bash
# Staging (com seed automático):
scripts/deploy.sh staging

# Produção (sem seed, com backup):
scripts/deploy.sh production
```

### Etapas do deploy automático

1. Verifica `SECRET_KEY` e `DATABASE_URL` no `.env`
2. Produção: tenta fazer `pg_dump` de backup
3. `docker compose build --no-cache` (Tailwind + Python)
4. `docker compose up -d` (web + redis + celery worker + celery beat)
5. Aguarda healthcheck (`/health/` responder 200)
6. Exibe logs recentes

### Variáveis de ambiente críticas

| Variável | Obrigatória | Descrição |
|----------|-------------|-----------|
| `SECRET_KEY` | ✅ Sim | Chave secreta Django |
| `DATABASE_URL` | Prod/Staging | `postgres://user:pass@host:5432/db` |
| `REDIS_URL` | Prod/Staging | `redis://host:6379/0` |
| `API_KEY_INSTANCIA` | WhatsApp | Token Evolution API |
| `INSTANCE` | WhatsApp | Nome da instância |
| `SEED_ON_START` | Opcional | `true` para popular banco no boot |
| `JWT_ACCESS_TOKEN_LIFETIME_MINUTES` | Opcional | Default 30 |

### Serviços Docker

| Serviço | Porta | Função |
|---------|-------|--------|
| `web` | 8000 | Django + Gunicorn |
| `querolink-redis` | 6379 | Broker Celery |
| `celery_worker` | — | Tarefas assíncronas (WhatsApp) |
| `celery_beat` | — | Tarefas agendadas |

---

## Estrutura do projeto

```
├── app/
│   ├── apps/
│   │   ├── accounts/     # Tenant, User (AbstractUser com roles)
│   │   ├── api/          # REST API (18 endpoints)
│   │   ├── audit/        # AuditLog
│   │   ├── commissions/  # CommissionPeriod, SellerCommission
│   │   ├── dashboard/    # Views desktop + mobile + gestor + financeiro
│   │   ├── notifications/# MessageTemplate, Notification, tasks
│   │   ├── orders/       # Order, PaymentLink
│   │   ├── payments/     # Payment
│   │   ├── sales/        # Sale
│   │   ├── sellers/      # Seller
│   │   └── webhooks/     # WebhookEvent (Pagar.me)
│   ├── config/           # settings, urls, celery, wsgi
│   └── services/
│       └── messaging/    # WhatsappClient (Evolution API)
├── scripts/              # deploy.sh
├── static/               # CSS, icons, manifest.json, sw.js
│   ├── css/              # input.css → tailwind.css (compilado)
│   └── icons/            # 8 PNGs (72-512px)
├── templates/
│   ├── base/             # Base templates (público, Bootstrap)
│   ├── components/       # Partial reutilizáveis (5)
│   ├── dashboard/        # Desktop (gestor, financeiro)
│   ├── mobile/           # Mobile (vendedor, 5 telas)
│   └── orders/           # Tela pública de link
├── requirements/         # base.txt, local.txt, production.txt
├── Dockerfile            # Multi-stage (Node build → Python)
├── docker-compose.yml    # 4 serviços
├── Makefile              # Comandos dev
├── entrypoint.sh         # Script de inicialização
├── seed.py               # Dados demo (16 vendedores Bibelô)
├── manage.py
└── README.md
```

---

## API REST

Swagger UI: `/api/schema/swagger-ui/`

| Método | URL | Permissão |
|--------|-----|-----------|
| POST | `/api/auth/login/` | Público (rate limit 5/min) |
| POST | `/api/auth/refresh/` | Público |
| GET/POST | `/api/sellers/` | MANAGER, ADMIN |
| GET/PATCH | `/api/sellers/{uuid}/` | MANAGER, ADMIN |
| POST | `/api/sellers/{uuid}/reset_password/` | MANAGER, ADMIN |
| GET/POST | `/api/sales/` | SELLER / MANAGER, ADMIN |
| GET | `/api/seller/sales/` | SELLER |
| GET | `/api/manager/sales/` | MANAGER, ADMIN |
| GET | `/api/manager/ranking/` | MANAGER, ADMIN |
| GET/POST | `/api/commissions/periods/` | MANAGER, ADMIN |
| POST | `/api/commissions/periods/{uuid}/close/` | MANAGER, ADMIN |
| POST | `/api/commissions/periods/{uuid}/send/` | MANAGER, ADMIN |
| POST | `/api/commissions/periods/{uuid}/approve/` | FINANCEIRO, ADMIN |
| POST | `/api/commissions/periods/{uuid}/reject/` | FINANCEIRO, ADMIN |
| POST | `/api/commissions/periods/{uuid}/mark_paid/` | FINANCEIRO, ADMIN |
| GET | `/api/financial/commissions/{uuid}/csv/` | FINANCEIRO, ADMIN |

---

## Telas

### Mobile (vendedor)
| Tela | URL |
|------|-----|
| Login | `/dashboard/mobile/login/` |
| Home | `/dashboard/mobile/` |
| Lançar venda | `/dashboard/mobile/lancar/` |
| Minhas vendas | `/dashboard/mobile/vendas/` |
| Meu desempenho | `/dashboard/mobile/desempenho/` |
| Esqueci senha | `/dashboard/mobile/forgot-password/` |

### Desktop (gestor/financeiro)
| Tela | URL |
|------|-----|
| Ranking | `/dashboard/gestor/ranking/` |
| Vendedores | `/dashboard/gestor/vendedores/` |
| Fechamento | `/dashboard/gestor/fechamento/` |
| Fila aprovação | `/dashboard/financeiro/fila/` |
| Histórico | `/dashboard/financeiro/historico/` |

### Legado (não alterado)
| Tela | URL |
|------|-----|
| Link público | `/` |
| Login admin | `/dashboard/login/` |

---

## Management commands

```bash
python manage.py reset_seller_password <seller_uuid>
```

---

## Documentação adicional

| Arquivo | Conteúdo |
|---------|----------|
| `PRD_QUEROLINK_COMISSOES.md` | PRD completo com todos os lotes |
| `VALIDATION_CHECKLIST_LOTE5.md` | 52 itens de validação (staging) |
| `READINESS_REPORT.md` | Relatório de prontidão para produção |

---

## Histórico de versões

| Versão | Data | Descrição |
|--------|------|-----------|
| 1.0.0-MVP | 2026-06-20 | MVP completo — 55 testes, 6 lotes implementados |

---

Desenvolvido por Bruno Vidal para Loja Bibelô.
