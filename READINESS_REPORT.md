# Readiness Report — VendaPay / QueroLink

> **Data:** 2026-07-01
> **Versão:** 2.1.0
> **Branch:** `querolink-v2`
> **Recomendação:** ✅ PRONTO PARA PRODUÇÃO

---

## Correções aplicadas (v2.1.0)

### Lote 7 — Concorrência e operação

| # | Severidade | O que foi corrigido |
|---|:----------:|---------------------|
| C1 | 🔴 | Deadlock em `close_seller_commissions` / `reopen_seller_commissions` / `pay_seller_commissions`: `select_for_update` sem `ORDER BY` podia travar duas transações concorrentes. Adicionado `.order_by('id')`. |
| C2 | 🔴 | Race condition em `create_commission_adjustment`: leitura e escrita do valor sem lock. Envolvido em `transaction.atomic()` com `select_for_update`. |
| C3 | 🟠 | `PaymentLink.gateway_link_id` sem `db_index`: full table scan em todo webhook. Adicionado índice + migration 0003. |
| C4 | 🟠 | Nenhuma task Celery tinha `time_limit`: uma task travada segurava o worker. Adicionados `soft_time_limit`/`time_limit` nas 4 tasks. |
| C5 | 🟠 | `cleanup_old_webhook_events` fazia DELETE massivo: podia travar tabela e encher WAL. Alterado para lotes de 1000 registros. |
| C6 | 🟠 | CSP bloqueava scripts inline: Alpine.js e modais quebravam. Adicionado `unsafe-inline` + `unsafe-eval` no script-src. |
| C7 | 🟢 | Webhook dedup via `gateway_event_id` (view + task + banco). |
| C8 | 🟢 | Limpeza de links órfãos no Pagar.me em caso de falha na criação. |

---

## Checklist de produção

| Item | Status |
|------|--------|
| DEBUG=False | ✅ |
| SECRET_KEY forte em produção | ✅ |
| HTTPS forçado | ✅ |
| HSTS 1 ano + subdomains + preload | ✅ |
| Session/CSRF cookies secure | ✅ |
| CSP ativo | ✅ |
| .env no .gitignore | ✅ |
| CORS configurado | ✅ (mesmo domínio) |
| Rate limiting ativo | ✅ |
| Migrations aplicadas | ✅ |
| Tasks Celery com time limits | ✅ |
| Tasks Celery com retry | ✅ |
| Backups automáticos (Google Drive) | ✅ |
| Monitoramento /health/ | ✅ |

---

## O que não está implementado

| Funcionalidade | Impacto |
|----------------|---------|
| Testes automatizados em CI (GitHub Actions) | Não bloqueante — Makefile `make test` manual |
| Alertas de falha de task Celery | Tasks falham silenciosamente (log apenas) |
| Painel de admin Django | Não necessário — tudo via dashboard customizado |
| Analytics de cliques (LinkClick) | Model existe, view não implementada |
| Tela de perfil do vendedor (troca de senha) | API `change-password` existe, UI não |

---

## Plano de deploy

```bash
git push origin querolink-v2
# Deploy automático via Coolify ou manual:
ssh <server>
cd /opt/querolink
git pull
scripts/deploy.sh production
```

---

## DR (Desaster Recovery)

| Cenário | Procedimento | RTO |
|---------|-------------|:---:|
| Falha de app | `docker compose restart web` | 5s |
| Falha de worker | `docker compose restart celery_worker` | 5s |
| Corrupção de dados | `scripts/restore.sh latest` (restaura backup do Google Drive) | 10 min |
| Perda total | `git pull` + `scripts/deploy.sh production` + restore | 30 min |
