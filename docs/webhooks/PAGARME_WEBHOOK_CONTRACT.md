# Webhook Pagar.me — Contrato

## Eventos suportados

| Evento | Efeito | Entidade |
|--------|--------|----------|
| `order.paid` | Payment → PAID, Order → COMPLETED | Link |
| `charge.paid` | Payment → PAID, Order → COMPLETED | Link |
| `payment-link.finished` | Payment → PAID, Order → COMPLETED | Link |
| `charge.paid` (boleto) | Boleto → PAGO via provider.parse_webhook | Boleto |
| `order.paid` (boleto) | Boleto → PAGO via provider.parse_webhook | Boleto |
| `order.payment_failed` | Payment → FAILED | Link |
| `charge.payment_failed` | Payment → FAILED | Link |
| `charge.payment_failed` (boleto) | Boleto → FALHOU (se CRIANDO/PENDENTE) | Boleto |
| `charge.refunded` | Payment → REFUNDED, Sale → ESTORNADA | Link |
| `charge.refunded` (boleto) | Boleto → ESTORNADO via mark_refunded | Boleto |
| `charge.chargedback` | Payment → CHARGEBACK, Sale → ESTORNADA | Link |
| `payment-link.expired` | Order → EXPIRED | Link |
| `payment-link.cancelled` | Order → CANCELED | Link |

## Idempotência

- `gateway_event_id`: um evento por ID (unique constraint)
- `effect_reference`: um efeito financeiro por `{tenant}:{provider_charge_id}:paid`

## Resposta HTTP

```
POST /api/webhooks/pagarme/{tenant_slug}/

200 → processador=merito-webhooks, receipt_id, correlation_id
400 → payload inválido
401/403 → autenticação
404 → tenant não encontrado
```

## Estados do WebhookEvent

RECEIVED → QUEUED → PROCESSING → PROCESSED
                              → FAILED → RECEIVED (reenqueue/recover)
                              → SKIPPED (ignorado)
                              → IGNORED (não suportado)

## Transições de boleto

PENDENTE → PAGO (webhook pagamento)
VENCIDO → PAGO (pagamento após vencimento)
PENDENTE → FALHOU (payment_failed)
PENDENTE → CANCEL_PEND → CANCELADO (cancelamento)
PAGO → ESTORNADO (refunded)
