# TODO pós-PR #27 — Prompt 52 Pagar.me

Data: 14/07/2026

## Estado salvo

- Branch: `fix/pagarme-webhook-reliability`
- Commit principal: `fac4237`
- PR draft: https://github.com/Brunohvg/QueroLink/pull/27
- Base: `querolink-v2`
- Suíte paralela final: `.venv/bin/python manage.py test --parallel 4 -v 1` passou com 759 testes e 1 skip.

## Continuação em 15/07/2026

- CI do PR #27 conferido: `backend-sqlite` e `backend-postgres` falhavam.
- Causa `backend-postgres`: `select_for_update()` tentava aplicar lock também ao lado nullable do join com `seller`.
- Correção: limitar locks críticos a `of=('self',)` nos pontos de correlação Pagar.me.
- Causa `backend-sqlite`: corrida de dupla confirmação podia bater em lock temporário ao criar/deduplicar `WebhookEvent`.
- Correção: criação idempotente com tentativas/backoff para `IntegrityError`/`OperationalError`.
- Gates locais pós-correção:
  - `manage.py test app.apps.webhooks -v 2`: OK, 29 testes.
  - `manage.py check`: OK.
  - `manage.py makemigrations --check --dry-run`: OK, sem alterações.
  - `git diff --check`: OK.

## Ainda pendente

1. Conferir CI do PR #27 no GitHub após push do ajuste de 15/07.
2. Rodar auditoria do Prompt 52 contra o PR #27 antes de mergear.
3. Conferir manualmente no app:
   - link pago aparece como `Pago`;
   - link recusado aparece como `Recusado`;
   - motivo da recusa aparece para vendedor;
   - WhatsApp/push de recusa leva o motivo quando o gateway enviar;
   - link pago não aparece em `Minhas vendas`;
   - link pago não soma semana, ranking nem meta do vendedor.
4. Validar comportamento com pagamento real/reprocessamento somente depois do merge/deploy.
5. Decidir se precisa PR separado de saneamento para `Sale LINK` legada já existente em produção. Neste PR ela ficou escondida das telas/somas operacionais, mas os registros antigos não foram apagados nem migrados.
6. Próximo PR recomendado: transformar `/gestor/webhooks/` em health root-only do sistema, fora da navegação normal do gestor.

## Não fazer ainda

- Não mergear sem auditoria do Prompt 52.
- Não iniciar Prompt 53 antes da auditoria do Prompt 52.
- Não apagar dados legados de `Sale LINK` sem decisão explícita e backup.
