# Mérito by Vidalys

Gestão de comissões para equipes de vendas no varejo físico.

Sistema multi-tenant para gestão de links de pagamento (Pagar.me) e comissões de vendedores. Inclui PWA para vendedores e dashboard administrativo completo.

**Em produção:** `querolink.lojabibelo.com.br` — Tenant: Loja Bibelô

---

## Funcionalidades principais

- **Vendedores:** cadastro individual ou importação CSV/XLSX com convite via WhatsApp.
- **Links de pagamento:** criação via Pagar.me v5 com link público por tenant.
- **Vendas manuais:** lançamento pelo vendedor via PWA mobile.
- **Comissões:** períodos mensais, fechamento, ajustes, pagamento e exportação contábil (CSV/XLSX/PDF).
- **Billing:** planos STARTER, PRO e BUSINESS com assinatura via Mercado Pago.
- **Notificações:** WhatsApp (Evolution API), push web, lembretes diários e e-mails de ciclo de vida.
- **Frete:** cotação oficial Correios CWS (PAC/SEDEX) com fallback estimado.
- **Backup automático:** diário às 02:00, pg_dump → Google Drive via rclone (R$ 0).
- **Webhooks:** Pagar.me e Mercado Pago com idempotência, dedup e retry.
- **Gestor:** dashboard com métricas, ranking, configurações e histórico de pagamentos.
- **Financeiro:** fila de aprovação e exportação contábil.

---

## Stack

| Camada | Tecnologia |
|--------|------------|
| Backend | Python 3.13, Django 5.1, DRF |
| Async | Celery 5.4 + Redis 7 |
| Auth | JWT (simplejwt) + Token + Session |
| Frontend | Alpine.js 3.14 + Tailwind CSS + Chart.js 4.4 |
| PWA | Service Worker + Web Manifest (8 ícones) |
| Banco | PostgreSQL |
| Gateway | Pagar.me Core v5 |
| WhatsApp | Evolution API |
| Deploy | Docker multi-stage, Gunicorn, Whitenoise |
| Backup | pg_dump + rclone → Google Drive (grátis) |
| Docs | Swagger UI (drf-spectacular) |

---

## Arquitetura

Aplicação multi-tenant por `Tenant`, com isolamento lógico em todos os models. Usuários possuem roles (ADMIN, MANAGER, FINANCEIRO, SELLER) vinculadas ao tenant.

```
┌────────────────────────────────────────────┐
│  Templates Django + Alpine.js + Tailwind   │
├────────────────────────────────────────────┤
│  Django REST Framework (API autenticada)   │
├──────────┬──────────┬──────────┬───────────┤
│ accounts │ sellers  │  sales   │commissions│
│  orders  │ payments │ webhooks │notifications│
│  billing │ freight  │  audit   │ dashboard │
├──────────┴──────────┴──────────┴───────────┤
│  PostgreSQL  │  Redis  │  Celery Worker    │
│  rclone/GDrive │ Celery Beat │ Gunicorn   │
└────────────────────────────────────────────┘
```

Integrações externas: Pagar.me (links de pagamento), Mercado Pago (assinaturas), Evolution API (WhatsApp), Correios CWS (frete).

---

## Instalação local

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements/base.txt
cp .env.example .env
make migrate
make dev
```

Acesse: `http://localhost:8000/dashboard/mobile/login/` (vendedor) ou `/dashboard/login/` (gestor/financeiro).

---

## Comandos rápidos (Makefile)

```bash
make help          # Todos os comandos
make dev           # Servidor dev (porta 8000)
make test          # Testes automatizados
make reset-db      # Recriar banco do zero
make build         # Build Docker
make up            # Iniciar containers
make down          # Parar containers
make logs          # Logs Docker
make clean         # Limpar pycache
```

---

## Execução de testes

```bash
make test
```

O comando executa a suíte completa via `python manage.py test app.apps.sellers app.apps.notifications app.apps.dashboard app.apps.api app.apps.commissions -v2`.

---

## Build CSS

```bash
make build-css
```

Compila o Tailwind CSS a partir de `static/css/tailwind.css` usando `npm run build:css`.

---

## Variáveis principais

| Variável | Obrigatória | Descrição |
|----------|:---:|-----------|
| `SECRET_KEY` | Sim | Chave secreta Django |
| `DATABASE_URL` | Sim | `postgres://user:pass@host:5432/db` |
| `FERNET_KEY` | Sim | Chave de criptografia (gere com `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`) |
| `REDIS_URL` | Sim | `redis://host:6379/0` |
| `SERVICE_FQDN_WEB` | Sim | Domínio público (ex: `querolink.lojabibelo.com.br`) |
| `API_KEY_PAGAR_ME` | Sim | Chave da API Pagar.me (raw `sk_*` ou base64) |
| `WHATSAPP_API_KEY` | WhatsApp | Token Evolution API |
| `WHATSAPP_INSTANCE` | WhatsApp | Nome da instância |
| `JWT_ACCESS_TOKEN_LIFETIME_MINUTES` | Não | Default 30 |

---

## Deploy

### Pré-requisitos

- Docker + Docker Compose
- `.env` configurado (copiar de `.env.example`, preencher valores reais)
- PostgreSQL externo (URL no `DATABASE_URL`)
- Rede Coolify (se usar Coolify): `docker network create coolify`

### Comando único

```bash
scripts/deploy.sh production
```

### Etapas do deploy

1. Verifica `SECRET_KEY`, `DATABASE_URL`, `FERNET_KEY` no `.env`
2. `docker compose build --no-cache` (Tailwind + Python + rclone)
3. `docker compose up -d` (web + redis + celery worker + celery beat)
4. Aguarda healthcheck (`/health/` responder 200)
5. Aplica migrations automaticamente (`entrypoint.sh`)
6. Exibe logs recentes

### Setup pós-deploy (1 vez)

1. Configure o rclone pelas variáveis descritas em [`docs/BACKUP_GDRIVE_RCLONE.md`](docs/BACKUP_GDRIVE_RCLONE.md).
2. Reinicie `celery_worker` e `celery_beat`.
3. Valide com `python manage.py backup_check` e `python manage.py backup_now` dentro do worker.
4. Configure o webhook no painel do Pagar.me:

```text
# URL: https://querolink.lojabibelo.com.br/api/webhooks/pagarme/<tenant_slug>/
# Eventos: charge.paid, charge.payment_failed, charge.refunded, charge.chargedback
# Para autenticação, configure usuário/senha no painel de configurações do tenant
```

### Serviços Docker

| Serviço | Porta | Função |
|---------|:---:|--------|
| `web` | 8000 | Django + Gunicorn |
| `querolink-redis` | 6379 | Broker Celery |
| `celery_worker` | — | Tarefas assíncronas (webhooks, WhatsApp, backup) |
| `celery_beat` | — | Tarefas agendadas (backup 02:00, limpeza eventos >90d) |

---

## Backup automático (Google Drive gratuito)

O sistema faz backup diário do PostgreSQL para o Google Drive via **rclone**.

**Guia completo:** [`docs/BACKUP_GDRIVE_RCLONE.md`](docs/BACKUP_GDRIVE_RCLONE.md)

Defina `RCLONE_CONFIG_GDRIVE_TOKEN` com o JSON inteiro gerado pelo rclone, incluindo o `refresh_token`. Defina também `GDRIVE_PATH`; o caminho recomendado para novas configurações é `merito-backups`.

| Característica | Detalhe |
|----------------|---------|
| Frequência | Diário às 02:00 (Celery beat) |
| Formato | `pg_dump -Fc -Z9` (compactado, ~5 MB) |
| Retenção Drive | 30 dias |
| Retenção local | 2 dias |
| Custo | R$ 0 (15 GB grátis Google Drive) |
| Setup | 1 vez: variáveis do rclone no `.env` |

**Restaurar:** `./scripts/restore.sh latest` ou `./scripts/restore.sh 2026-06-29`

---

## Webhooks Pagar.me

Eventos tratados com idempotência (dedup via `gateway_event_id`), Celery retry (3x, 30s) e time limits:

| Evento | Ação |
|--------|------|
| `charge.paid` | Payment → PAID, Order → COMPLETED, cria Sale, notifica WhatsApp |
| `charge.payment_failed` | Payment → FAILED, notifica vendedor com motivo da recusa |
| `charge.refunded` | Payment → REFUNDED, remove Sale, notifica vendedor |
| `charge.chargedback` | Payment → CHARGEBACK, remove Sale, notifica vendedor |
| `charge.antifraud_*` | Log do antifraude; se reprovado → Payment FAILED |
| `order.paid` | Mesmo fluxo de charge.paid (via charges[0]) |
| `payment-link.finished` | Mesmo fluxo de charge.paid (via gateway_link_id) |
| `payment-link.expired` | Order → EXPIRED, notifica vendedor |
| `payment-link.cancelled` | Order → CANCELED, notifica vendedor |

URL: `POST /api/webhooks/pagarme/<tenant_slug>/` (com autenticação Basic Auth opcional por tenant)

Dedup: eventos com mesmo `id` (ex: `evt_xxx`) são ignorados após o primeiro processamento.

---

## Estrutura do projeto

```
├── app/
│   ├── apps/
│   │   ├── accounts/     # Tenant, User (roles), middleware CSP, backup task
│   │   ├── api/          # REST API (25+ endpoints, rate limiting)
│   │   ├── audit/        # AuditLog
│   │   ├── commissions/  # CommissionPeriod, SellerCommission, services
│   │   ├── dashboard/    # Views desktop + mobile + gestor + financeiro
│   │   ├── notifications/# MessageTemplate, Notification, WhatsApp tasks
│   │   ├── orders/       # Order, PaymentLink, services (link creation)
│   │   ├── payments/     # Payment (paid_at, card_brand, card_last4)
│   │   ├── sales/        # Sale (LINK + MANUAL origins)
│   │   ├── sellers/      # Seller
│   │   └── webhooks/     # WebhookEvent, Pagar.me handler, cleanup task
│   ├── config/           # settings, urls, celery, wsgi
│   └── services/
│       ├── gateway/      # PagarMeGateway (API v5)
│       └── messaging/    # WhatsappClient (Evolution API)
├── scripts/              # backup.sh, restore.sh, setup-rclone.sh, deploy.sh
├── static/               # CSS (Tailwind), JS, icons, manifest.json, sw.js
├── templates/
│   ├── dashboard/        # Desktop (gestor, financeiro, link detalhe)
│   ├── mobile/           # Mobile PWA (vendedor, 5 telas)
│   ├── orders/           # Tela pública de link (Tailwind)
│   └── components/       # Partials reutilizáveis
├── requirements/         # base.txt, local.txt, production.txt
├── Dockerfile            # Multi-stage (Node build → Python + rclone)
├── docker-compose.yml    # 4 serviços + 4 volumes
├── Makefile              # Comandos dev
├── entrypoint.sh         # Boot (migrate, superuser, collectstatic)
├── manage.py
├── README.md
└── docs/
    ├── BACKUP_GDRIVE_RCLONE.md
    └── GOOGLE_DRIVE_CREDENTIALS.md  # Redirecionamento legado
```

---

## API REST

Swagger UI: `/api/schema/swagger-ui/`

### Autenticação

| Método | URL | Permissão |
|--------|-----|-----------|
| POST | `/api/auth/login/` | Público (rate limit 5/min) |
| POST | `/api/auth/refresh/` | Autenticado |
| POST | `/api/auth/logout/` | Autenticado (blacklist do refresh token) |

### Sellers

| Método | URL | Permissão |
|--------|-----|-----------|
| GET/POST | `/api/sellers/` | MANAGER, ADMIN |
| GET/PUT/PATCH/DELETE | `/api/sellers/{uuid}/` | MANAGER, ADMIN |
| POST | `/api/sellers/{uuid}/reset_password/` | MANAGER, ADMIN |
| POST | `/api/sellers/import_sellers/` | MANAGER, ADMIN (CSV/XLSX) |
| GET | `/api/manager/seller/{uuid}/` | MANAGER, ADMIN |
| GET | `/api/manager/seller/{uuid}/csv/` | MANAGER, ADMIN |
| GET | `/api/manager/seller/{uuid}/xlsx/` | MANAGER, ADMIN |
| GET | `/api/manager/seller/{uuid}/pdf/` | MANAGER, ADMIN |

### Sales

| Método | URL | Permissão |
|--------|-----|-----------|
| GET/POST | `/api/sales/` | SELLER / MANAGER, ADMIN |
| GET/PUT/DELETE | `/api/sales/{uuid}/` | SELLER (própria) / MANAGER |
| GET | `/api/seller/sales/` | SELLER |
| GET | `/api/manager/sales/` | MANAGER, ADMIN |

### Links de pagamento

| Método | URL | Permissão |
|--------|-----|-----------|
| GET/POST | `/api/seller/links/` | SELLER (rate limit 10/min) |

### Comissões

| Método | URL | Permissão |
|--------|-----|-----------|
| GET/POST | `/api/commissions/periods/` | MANAGER, ADMIN |
| GET/PATCH/DELETE | `/api/commissions/periods/{uuid}/` | MANAGER, ADMIN |
| POST | `/api/commissions/periods/{uuid}/sync/` | MANAGER, ADMIN |
| POST | `/api/commissions/periods/{uuid}/close_sellers/` | MANAGER, ADMIN |
| POST | `/api/commissions/periods/{uuid}/reopen_sellers/` | MANAGER, ADMIN |
| POST | `/api/commissions/periods/{uuid}/pay_sellers/` | ADMIN, FINANCEIRO |
| POST | `/api/commissions/periods/{uuid}/cancel/` | MANAGER, ADMIN |
| GET | `/api/manager/commissions/{status}/` | MANAGER, ADMIN |
| GET | `/api/manager/ranking/` | MANAGER, ADMIN |
| GET | `/api/manager/ranking/annual/` | MANAGER, ADMIN |
| GET | `/api/manager/dashboard/summary/` | MANAGER, ADMIN |

### Financeiro

| Método | URL | Permissão |
|--------|-----|-----------|
| GET | `/api/financial/payment-queue/` | FINANCEIRO, ADMIN |
| GET | `/api/financial/commissions/{uuid}/csv/` | FINANCEIRO, ADMIN |

### Configuração

| Método | URL | Permissão |
|--------|-----|-----------|
| GET | `/api/manager/webhook-status/` | MANAGER, ADMIN |
| POST | `/api/seller/change-password/` | SELLER |

---

## Telas

### Mobile PWA (vendedor)

| Tela | URL |
|------|-----|
| Login | `/dashboard/mobile/login/` |
| Home (resumo do mês) | `/dashboard/mobile/` |
| Lançar venda | `/dashboard/mobile/lancar/` |
| Minhas vendas | `/dashboard/mobile/vendas/` |
| Links de pagamento | `/dashboard/mobile/links/` |
| Fechamento | `/dashboard/mobile/fechamento/` |
| Esqueci senha | `/dashboard/mobile/forgot-password/` |

### Desktop (gestor)

| Tela | URL |
|------|-----|
| Login | `/dashboard/login/` |
| Home (dashboard) | `/dashboard/gestor/` |
| Ranking | `/dashboard/gestor/ranking/` |
| Vendedores | `/dashboard/gestor/vendedores/` |
| Links | `/dashboard/gestor/links/` |
| Detalhe do link | `/dashboard/gestor/links/{uuid}/` |
| Fechamento | `/dashboard/gestor/fechamento/` |
| Configurações | `/dashboard/gestor/configuracoes/` |

### Desktop (financeiro)

| Tela | URL |
|------|-----|
| Fila de aprovação | `/dashboard/financeiro/fila/` |
| Histórico de pagamentos | `/dashboard/financeiro/historico/` |

### Público

| Tela | URL |
|------|-----|
| Link de pagamento | `/<tenant_slug>/` |
| Pagamento concluído | `/pago/<order_uuid>/` |

---

## Management commands

```bash
python manage.py reset_seller_password <seller_uuid>
```

---

## Documentação adicional

| Arquivo | Conteúdo |
|---------|----------|
| `PRD_QUEROLINK_COMISSOES.md` | PRD completo com especificação de todos os lotes |
| `API.md` | Documentação detalhada da API REST |
| `READINESS_REPORT.md` | Relatório de prontidão para produção |

---

## Histórico de versões

| Versão | Data | Descrição |
|--------|------|-----------|
| 2.1.0 | 2026-07-01 | Correções de concorrência, dedup de webhooks, CSP fix, time limits em tasks, cleanup batch |
| 2.0.0 | 2026-06-29 | Estabilização: 16 correções críticas/altas, webhook completo, backup automático, redesign UI |
| 1.0.0 | 2026-06-20 | MVP — 55 testes, 6 lotes implementados |

---

## Propriedade intelectual

Desenvolvido por Bruno Vidal para Loja Bibelô. Todos os direitos reservados.
