# QueroLink — Sistema de Comissões Multi-Tenant

Sistema de gestão de comissões para vendedores de lojas.
Tenant principal: Loja Bibelô (16 vendedores reais).

---

## Stack

| Camada | Tecnologia |
|--------|------------|
| Backend | Python 3.12, Django 5.1, Django REST Framework |
| Async | Celery 5.4 + Redis 7 |
| Auth | JWT (simplejwt) + Token + Session |
| Frontend mobile | Alpine.js + Tailwind CSS (CDN) |
| Frontend desktop | Django Templates (futuro: Tailwind build) |
| PWA | Service Worker + Web Manifest |
| Banco | SQLite (dev) / PostgreSQL (prod) |
| Deploy | Docker, Gunicorn, Whitenoise |
| APIs externas | Pagar.me v5, Evolution API (WhatsApp) |
| Docs API | Swagger UI (drf-spectacular) |

---

## Decisão de frontend mobile: Alpine.js

**Escolha:** Alpine.js sobre HTMX para as telas mobile do vendedor.

**Justificativa:**
- A API REST do Lote 2 retorna JSON. Alpine.js consome JSON nativamente via `fetch()`, enquanto HTMX espera HTML do servidor — exigiria endpoints HTML duplicados ou renderização server-side adicional.
- Alpine.js oferece reatividade local (`x-data`, `x-model`, `x-show`, `x-for`) sem build step, ideal para telas como "Lançar Venda" (máscara monetária, feedback instantâneo) e "Minhas Vendas" (agrupamento por data, exclusão condicional).
- Dispositivos móveis de loja são frequentemente compartilhados entre turnos; a autenticação via sessão Django (cookie de sessão) é mais segura que JWT em localStorage.
- Para comunicação com a API: as telas mobile usam sessão Django (`SessionAuthentication` adicionado ao DRF). O JWT permanece disponível para consumidores externos (app nativo futuro, integrações).

---

## Setup rápido (dev)

```bash
python3 -m venv .venv && source .venv/bin/activate
uv pip install -r requirements/base.txt
cp .env.example .env
python manage.py migrate
python manage.py runserver
```

### Popular banco local (demo)

```bash
python manage.py shell < seed.py
```

---

## Estrutura

```
app/
├── apps/
│   ├── accounts/     # Tenant, User (AbstractUser com roles)
│   ├── api/          # REST API (Lote 2)
│   ├── commissions/  # CommissionPeriod, SellerCommission
│   ├── dashboard/    # Dashboard gestor + telas mobile
│   ├── notifications/# MessageTemplate, Notification, tasks Celery
│   ├── orders/       # Order, PaymentLink
│   ├── payments/     # Payment
│   ├── sales/        # Sale
│   ├── sellers/      # Seller
│   └── webhooks/     # WebhookEvent (Pagar.me)
├── config/           # settings, urls, celery
├── services/
│   └── messaging/    # WhatsappClient (Evolution API)
static/
├── manifest.json     # PWA manifest
├── sw.js             # Service Worker
templates/
├── base/             # Base templates
├── dashboard/        # Desktop dashboard
├── mobile/           # Telas mobile (vendedor) — Lote 3
└── orders/           # Tela pública de link
```

---

## API REST

Swagger UI: `/api/schema/swagger-ui/`

Autenticação: JWT (`/api/auth/login/`), Token, Session.

Rate limit login: 5 tentativas/minuto (IP + username).

### Endpoints principais

| Método | URL | Permissão |
|--------|-----|-----------|
| POST | `/api/auth/login/` | Público |
| GET/POST | `/api/sales/` | SELLER/MANAGER/ADMIN |
| GET | `/api/seller/sales/` | SELLER |
| GET | `/api/manager/sales/` | MANAGER/ADMIN |
| GET/POST | `/api/sellers/` | MANAGER/ADMIN |
| GET/POST | `/api/commissions/periods/` | MANAGER/ADMIN |

---

## Comandos de management

```bash
python manage.py reset_seller_password <seller_uuid>
```

---

## Variáveis de ambiente

Ver `.env.example`. Principais: `SECRET_KEY`, `DEBUG`, `DATABASE_URL` (prod), `REDIS_URL`, `API_KEY_PAGAR_ME`, `API_KEY_INSTANCIA`, `INSTANCE`, `JWT_ACCESS_TOKEN_LIFETIME_MINUTES`, `JWT_REFRESH_TOKEN_LIFETIME_DAYS`.

---

## Testes

```bash
python manage.py test app.apps.sellers app.apps.notifications app.apps.dashboard app.apps.api
```

55 testes. Cobertura: models, migrations, API auth, CRUD, multi-tenant isolation, máquina de estados de comissão, WhatsApp notifications.
