# Relatório de Prontidão para Produção — QueroLink

> Data: 2026-06-29 | Versão: 2.0.0

---

## Recomendação

**✅ PRONTO PARA PRODUÇÃO** (com 2 ações manuais únicas)

O sistema está completo e estabilizado — fluxo ponta a ponta funcional, webhooks tratados com idempotência, backup automático configurável.

---

## Resumo do sistema

| Componente | Quantidade |
|---|---|
| Apps Django | 11 implementados |
| Migrations | 30+ (inclui payments 0002 com paid_at/card_brand/card_last4) |
| Telas mobile PWA | 6 (login, home, lançar venda, minhas vendas, links, fechamento) |
| Telas desktop | 8 (dashboard, ranking, vendedores, links, link detalhe, fechamento, configurações, login) |
| Telas financeiro | 2 (fila aprovação, histórico pagamentos) |
| Telas públicas | 2 (link pagamento, pagamento concluído) — Tailwind redesign |
| Endpoints API | 25+ (auth + CRUD + links + ranking + commissions + CSV/Excel/PDF) |
| Eventos webhook | 9 (charge.paid, order.paid, payment-link.finished, charge.payment_failed, charge.refunded, charge.chargedback, payment-link.expired, payment-link.cancelled, charge.antifraud_*) |
| Integrações | Pagar.me Core v5 (API + webhooks), Evolution API (WhatsApp) |
| Backup | pg_dump + rclone → Google Drive (02:00 diário, R$ 0) |

---

## O que foi estabilizado (Lote 6 — 29/Jun)

### Crítico
- **C1**: UnboundLocalError no handler `payment-link.expired` corrigido (crash Celery)
- **C2**: Sale removida em `charge.refunded` e `charge.chargedback` (relatórios não inflam mais)
- **C3**: CSRF token injetado automaticamente em todos `fetch()` do mobile PWA
- **C4**: Webhook sem assinatura aceito com WARNING (Pagar.me não envia por padrão)
- **C5**: Endpoint `auth/logout/` com JWT TokenBlacklistView

### Alto
- **H3**: Middleware `TrialEnforcementMiddleware` (aviso quando trial expira)
- **H4**: Cálculo de comissão unificado em `get_commission_rate()`
- **H7**: `_normalize_api_key` — `rstrip(':')` substituído por remoção exata do `:`

### Médio
- **M1**: Páginas públicas (`orders/index.html`, `payment_success.html`) migradas para Tailwind
- **M2**: Rate limit 10/min em `POST /api/seller/links/`
- **M5**: Celery beat `cleanup_old_webhook_events` (diário, >90 dias)
- **M6**: Anos dinâmicos no filtro do dashboard (2024-2028 → dinâmico ±2 anos)
- **M7**: Validação de valor máximo R$ 100.000 em venda manual (API + mobile)
- **M8**: Template tag `currency_filters.brl` (evita 500 no mobile)
- **M10**: `short_code` gerado no `create_payment_link()`

### Features novas
- **Payment metadata**: campos `paid_at`, `card_brand`, `card_last4` extraídos do webhook
- **Link detail redesign**: card premium com avatar, telefone, bandeira cartão, adquirente, status condicional
- **Backup automático**: pg_dump → rclone → Google Drive (Celery beat 02:00)
- **PaymentLink.finished handler**: webhook de finalização de link agora tratado

---

## Checklist de produção

### ✅ Pronto (sem ação necessária)

| Item | Status |
|---|---|
| Migrations aplicam em ordem | ✅ |
| Webhooks Pagar.me — todos eventos tratados | ✅ |
| Idempotência em webhooks duplicados | ✅ |
| Celery worker com retry 3x + beat schedule | ✅ |
| CSRF mobile PWA — todos fetch() | ✅ |
| Rate limit API links (10/min) | ✅ |
| Limite de vendedores por plano (API) | ✅ |
| Middleware de trial | ✅ |
| Cálculo de comissão unificado | ✅ |
| Telas públicas Tailwind | ✅ |
| Link detail com dados completos do cartão | ✅ |
| .env sem indentação quebrada | ✅ |
| Backup scripts prontos | ✅ |

### 🔴 Ação manual necessária (1 vez)

| Ação | Comando |
|---|---|
| Autenticar rclone no Google Drive | `docker exec -it <web> ./scripts/setup-rclone.sh` |
| Configurar webhook Pagar.me | Painel Pagar.me → Developers → Webhooks → URL + eventos |
| Rodar migration 0002 | Automático no boot (`entrypoint.sh` faz `migrate`) |

### 🟡 Recomendado

| Ação | Por que |
|---|---|
| Configurar webhook secret no Pagar.me | Habilita verificação HMAC (X-Hub-Signature-256) |
| Configurar `DJANGO_SUPERUSER_EMAIL/PASSWORD` | Senha fixa ao invés de aleatória |
| Configurar `EMAIL_HOST/USER/PASSWORD` no .env | Ou trocar production.py para console.EmailBackend |

---

## O que NÃO está implementado

| Feature | Motivo |
|---------|--------|
| Verificação de email no cadastro | Sem servidor de email configurado |
| Recuperação de senha desktop (gestor/admin) | Sem email. Reset WhatsApp existe para seller |
| Analytics de cliques (LinkClick) | Model existe, view não implementada. Baixa prioridade |
| Fiscalização de trial (bloqueio) | Só aviso (WARNING). Bloqueio requer sistema de cobrança |
| Testes automatizados para webhook/gateway | Cobertura atual: sellers, notifications, dashboard, api |

---

## Plano de deploy

```bash
# 1. Atualizar repositório no servidor
git pull origin querolink-v2

# 2. Rebuild + restart
docker compose down
docker compose build --no-cache
docker compose up -d

# 3. Verificar health
curl http://localhost:8000/health/

# 4. Setup rclone (1 vez)
docker exec -it <container-web> ./scripts/setup-rclone.sh

# 5. Configurar webhook Pagar.me (1 vez)
# URL: https://querolink.lojabibelo.com.br/api/webhooks/pagarme/artesanatos-bibelo-ltda/
# Eventos: charge.paid, charge.payment_failed, charge.refunded, charge.chargedback

# 6. Testar backup
docker exec <container-celery_beat> /app/scripts/backup.sh
```

---

## Tempo de recuperação em desastre

| Cenário | Tempo | Procedimento |
|---------|:---:|------|
| Container caiu | < 30s | Docker auto-restart (`restart: always`) |
| Banco corrompido | < 1 min | `./scripts/restore.sh latest` |
| Servidor perdido | < 10 min | Novo servidor + `git clone` + restore dump + `docker compose up -d` |

---

Desenvolvido por Bruno Vidal.
