# Lote 5 — Checklist de Validação em Staging

> Data: 2026-06-20 | Executor: Bruno Vidal
> Status geral: ✅ Pronto para staging (itens locais passam; ambiente Coolify pendente)

---

## 1. Ambiente

| # | Item | Local | Staging | Evidência |
|---|---|---|---|---|
| 1.1 | Banco isolado da produção | ✅ SQLite local | ⏳ PostgreSQL staging no Coolify | — |
| 1.2 | `SECRET_KEY` próprio | ✅ | ⏳ | — |
| 1.3 | `API_KEY_PAGAR_ME` de teste | ✅ | ⏳ | — |
| 1.4 | `API_KEY_INSTANCIA` de teste | ✅ | ⏳ | — |
| 1.5 | Domínio staging | — | ⏳ | Confirmar com usuário |

---

## 2. Migrations (local → PostgreSQL)

| # | Item | Status | Detalhe |
|---|---|---|---|
| 2.1 | Todas migrations aplicam em ordem | ✅ | 12 migrations de projeto, todas `[X]` |
| 2.2 | Data migration `0003_link_sellers_to_users` não quebra | ✅ | "Nenhum Seller sem User" — pulada sem erro |
| 2.3 | `NOT NULL` constraint em `Seller.user` respeitada | ✅ | Seed atualizado para criar User junto com Seller |
| 2.4 | Audit migration `0001_initial` aplica | ✅ | Primeira migration do app audit |
| 2.5 | Notification templates padrão criados | ✅ | `0002_create_default_templates` por tenant |

**Ordem correta verificada:**
```
accounts → sellers (4 migrations: 0001→0002→0003→0004) →
orders → notifications (2 migrations) → payments → sales → audit
```

---

## 3. Validação funcional de ponta a ponta

| # | Item | Local | Evidência |
|---|---|---|---|
| 3.1 | Link público (`/`) intacto | ✅ (não alterado) | Template `orders/index.html` sem modificações |
| 3.2 | Cadastrar vendedor (gestor) | ✅ | `POST /api/sellers/` → 201, username + senha gerados |
| 3.3 | Seed 16 vendedores Bibelô | ✅ | Todos com `User` + `Seller` + senha temporária |
| 3.4 | Login mobile vendedor | ✅ | `POST /dashboard/mobile/login/` → 302 para home |
| 3.5 | Lançar venda (R$ 47,90) | ✅ | 4790 centavos, `47.90` no round-trip |
| 3.6 | Lançar venda (R$ 19,90) | ✅ | 1990 centavos, `19.90` no round-trip |
| 3.7 | Lançar venda (R$ 125,50) | ✅ | 12550 centavos, `125.50` no round-trip |
| 3.8 | Ranking gestor reflete vendas | ✅ | API `/api/manager/ranking/` responde com `sum` |
| 3.9 | Fechar mês (`recalculate()`) | ✅ | 57990 == 57990 (manual == automático) |
| 3.10 | Enviar ao financeiro | ✅ | `EM_CONFERENCIA` → `ENVIADA_FINANCEIRO` |
| 3.11 | Financeiro aprova | ✅ | `ENVIADA_FINANCEIRO` → `APROVADA` |
| 3.12 | Financeiro marca pago | ✅ | `APROVADA` → `PAGA` |
| 3.13 | Notificação WhatsApp comissão paga | ⏳ | Pendente: testar com número real em staging |
| 3.14 | Notificação WhatsApp vendedor novo | ⏳ | Pendente: testar com número real em staging |
| 3.15 | Isolamento multi-tenant | ✅ | 5 testes automatizados passam (Lote 2) |
| 3.16 | PWA instalável (manifest) | ✅ | `manifest.json` válido, 8 ícones |
| 3.17 | PWA service worker | ✅ | `sw.js` com cache + offline fallback |
| 3.18 | Falha WhatsApp não bloqueia | ✅ | Try/except em `notify_seller_credentials` |
| 3.19 | Logs Celery sem erros | ⏳ | Pendente: verificar no Coolify |
| 3.20 | Swagger API funcional | ✅ | `GET /api/schema/` → 200 |

---

## 4. Consistência de dados

| # | Item | Status | Detalhe |
|---|---|---|---|
| 4.1 | Soma `recalculate()` == soma manual `Sale` | ✅ | 57990 == 57990 |
| 4.2 | Centavos não perdem precisão | ✅ | R$ 47,90 → 4790 → R$ 47.90 |
| 4.3 | User vinculado a Seller (1:1) | ✅ | 16 sellers = 16 users SELLER |
| 4.4 | `commission_rate` herdado do Tenant | ✅ | `default_commission_rate=0.01` → sellers |
| 4.5 | Status máquina de estados respeitada | ✅ | `ABERTA→EM_CONFERENCIA→ENVIADA→APROVADA→PAGA` |

---

## 5. Testes automatizados

| # | Item | Status |
|---|---|---|
| 5.1 | `app.apps.sellers` (10 testes) | ✅ |
| 5.2 | `app.apps.notifications` (14 testes) | ✅ |
| 5.3 | `app.apps.dashboard` (6 testes) | ✅ |
| 5.4 | `app.apps.api` (25 testes) | ✅ |
| 5.5 | Total: **55/55 passam** | ✅ |

---

## 6. Pendências para staging (requer acesso ao Coolify)

| # | Item | Responsável | Prioridade |
|---|---|---|---|
| 6.1 | Criar app staging no Coolify | Usuário | 🔴 Alta |
| 6.2 | Provisionar PostgreSQL staging | Usuário | 🔴 Alta |
| 6.3 | Configurar `.env` staging (credenciais separadas) | Usuário | 🔴 Alta |
| 6.4 | Rodar `migrate` contra PostgreSQL | Usuário | 🔴 Alta |
| 6.5 | Rodar `seed` em staging | Usuário | 🟡 Média |
| 6.6 | Testar WhatsApp com número real de teste | Usuário | 🟡 Média |
| 6.7 | Testar PWA instalação em celular real | Usuário | 🟡 Média |
| 6.8 | Verificar logs Celery no Coolify | Usuário | 🟡 Média |
| 6.9 | Confirmar domínio staging | Usuário | 🟢 Baixa |
