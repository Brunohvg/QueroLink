# Diagnóstico antes — Prompt 52 Pagar.me

Base auditada: `querolink-v2` em `5859b1a3bafa93dbaa6c7bb1018b01cc0c63d62e`.

## Reprodução sanitizada

Evento usado:

- `type`: `charge.paid`
- `id`: `evt_diag_charge_paid`
- `data.id`: `ch_sanitizado`
- `data.order.code`: UUID local do `Order`
- cliente, link e ids externos sanitizados

Execução em banco de teste temporário:

```text
RuntimeError: WHATSAPP_SANITIZED_FAILURE
```

Trecho exato do traceback:

```text
File "app/apps/webhooks/tasks.py", line 267, in process_pagarme_webhook
  notify_seller_link_status(order.seller, order, 'payment_paid')
File "app/apps/notifications/tasks.py", line 193, in notify_seller_link_status
  create_and_send_notification(...)
RuntimeError: WHATSAPP_SANITIZED_FAILURE
```

Estado observado após a exceção:

```text
processed=False order_status=PENDING payment_status=PENDING sale_count=0
```

## Linha causadora

`app/apps/webhooks/tasks.py:267`

A task chama `notify_seller_link_status(...)` dentro de `transaction.atomic()` e antes de marcar o evento como processado.

## Mapa atual do fluxo

1. `pagarme_webhook` recebe o payload e grava `WebhookEvent`.
2. `process_pagarme_webhook` abre `transaction.atomic()`.
3. Localiza `Order` por `order.code`, `payment_link_id` ou `Payment.gateway_*`.
4. Atualiza `Payment`.
5. Atualiza `Order`.
6. Cria `Sale` LINK.
7. Envia WhatsApp/push via `notify_seller_link_status`.
8. Só depois marca `WebhookEvent.processed=True`.

## Causa raiz

Persistência financeira e efeito externo estão acoplados na mesma transação. Qualquer falha em WhatsApp/push após a resolução do pagamento aborta a transação inteira, desfaz `Payment`, `Order` e `Sale`, e deixa o evento sem processar. O retry repete a mesma falha.

## Risco confirmado

Um pagamento real confirmado no Pagar.me pode ficar pendente no sistema mesmo com webhook HTTP 200, porque o recebimento do webhook não garante que a persistência financeira foi commitada.
