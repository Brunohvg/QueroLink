# Auditoria — Webhooks Pagar.me: Links e Boletos Confiáveis

## Causa Raiz

### 1. Eventos ficavam PENDING sem processamento automático

A task `process_pagarme_webhook` não possuía try/except no nível correto. Exceções que ocorriam dentro do `transaction.atomic()` propagavam-se para o Celery (que fazia retry), mas ao esgotar as tentativas o `WebhookEvent` permanecia em estado RECEIVED/PROCESSING indefinidamente.

Além disso, o estado `PROCESSING` era definido por `process_paid_pagarme_event` (função interna), sem um mecanismo de recuperação para eventos travados nesse estado.

### 2. charge.paid e order.paid não tinham idempotência de efeito

Dois eventos para o mesmo pagamento (ex: `charge.paid` + `order.paid`) eram processados separadamente, sem deduplicação baseada no efeito financeiro. O segundo evento poderia ser redundante mas o sistema tratava cada um isoladamente.

### 3. Resposta HTTP não identificável como Mérito

A resposta ao Pagar.me era `{"status": "received"}` sem headers ou corpo que identificassem o processador, impossibilitando verificação via `response_raw` no painel do Pagar.me.

### 4. Ausência de watchdog para eventos travados

Eventos em PROCESSING por mais de N minutos não eram recuperados automaticamente.

## Correções

### Fluxo anterior
```
HTTP POST → persistir → .delay() sem transaction.on_commit → 
task sem try/except → evento fica RECEIVED/PROCESSING travado
```

### Fluxo corrigido
```
HTTP POST → gerar receipt_id/correlation_id → persistir → .delay() →
task com try/except → PROCESSED ou FAILED →
watchdog recupera PROCESSING travados →
resposta HTTP identificável com processor=merito-webhooks
```

## Arquivos alterados

| Arquivo | Alteração |
|---------|-----------|
| `app/apps/webhooks/models.py` | +5 campos (receipt_id, correlation_id, processor_version, effect_reference, queued_at), +1 estado (IGNORED), +1 index |
| `app/apps/webhooks/migrations/0007_webhookevent_correlation_id_and_more.py` | Migration progressiva |
| `app/apps/webhooks/services.py` | +apply_pagarme_payment_event (serviço único), +check_effect_applied, +mark_effect_applied, +generate_receipt_id, +generate_correlation_id, +build_effect_reference. Refatorado process_paid_pagarme_event para usar serviço único |
| `app/apps/webhooks/tasks.py` | +try/except em process_pagarme_webhook, +watchdog_stuck_webhooks, +reconcile_pending_boletos, estado QUEUED ao iniciar processamento |
| `app/apps/webhooks/views.py` | Resposta HTTP com headers X-Merito-*, corpo JSON com processor/version/receipt_id/correlation_id/duplicate |
| `app/apps/webhooks/tests.py` | Atualizadas assertions para novo formato de resposta |

## Serviço único

`apply_pagarme_payment_event()` — usado por:
- Webhook (`process_paid_pagarme_event` → `_inner_process_paid`)
- Retry (Celery autoretry)
- Verificação manual (`gestor_link_verificar_pagamento` → `process_paid_pagarme_event`)
- Reconciliacão (`reconcile_pending_orders` → `process_paid_pagarme_event`)
- Watchdog (`watchdog_stuck_webhooks`)

## ID de efeito

Formato: `pagarme:{tenant_uuid}:{provider_charge_id}:paid`

Permite que `charge.paid` e `order.paid` para o mesmo pagamento sejam deduplicados.

## Resposta HTTP ao Pagar.me

```json
{
  "received": true,
  "processor": "merito-webhooks",
  "version": "v1",
  "event_id": "hook_...",
  "receipt_id": "whr_...",
  "correlation_id": "...",
  "duplicate": false
}
```

Headers: `X-Merito-Webhook`, `X-Merito-Receipt-Id`, `X-Correlation-Id`

## Watchdog

Recupera eventos:
- PROCESSING há mais de 10 minutos → retorna a RECEIVED e reenfileira
- RECEIVED há mais de 5 minutos sem processamento → reenfileira

## Reconciliacão de boletos

Nova task `reconcile_pending_boletos` que consulta o provider para boletos PENDENTE/VENCIDO com provider_order_id definido.

## Migrations

1 migration (0007): adiciona receipt_id, correlation_id, processor_version, effect_reference, queued_at, estado IGNORED, index status+processing_started_at.

## Testes

- 39 testes de webhook: OK
- 258 testes (payments, orders, receivables, commissions): OK
- Suíte completa (971): OK

## Customer Ledger

NÃO alterado.
