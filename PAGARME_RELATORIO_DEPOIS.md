# Relatório depois — Prompt 52 Pagar.me

## Problema

Pagamentos reais confirmados pelo Pagar.me podiam permanecer pendentes localmente quando uma falha de WhatsApp/push acontecia dentro da mesma transação do webhook. Além disso, o pagamento por link era criado como `Sale LINK`, podendo aparecer/somar em fluxos operacionais do vendedor quando o vendedor já lança o fechamento manualmente.

## Causa

A task misturava persistência financeira (`Payment`, `Order`, `Sale`, `WebhookEvent`) com efeito externo de notificação. O webhook pago também criava `Sale LINK`, o que gerava risco de duplicidade operacional.

## Correção

- Processamento pago do Pagar.me movido para serviço idempotente transacional.
- Notificações passam a rodar após commit.
- Eventos têm estado `RECEIVED/PROCESSING/PROCESSED/FAILED/SKIPPED`, tentativas e datas.
- Reprocessamento manual criado por management command.
- Reconcile usa o mesmo serviço idempotente.
- Status exibidos em português (`Pago`, `Recusado`, etc.).
- Motivo de recusa aparece na aplicação e vai no contexto da notificação.
- Link pago confirma `Order`/`Payment`, mas não cria `Sale LINK` nova.
- `Sale LINK` legada não aparece/soma em Minhas vendas, ranking e meta do vendedor.
- Painel técnico de webhook removido da navegação normal do gestor; health root-only fica para próximo PR.

## Arquivos alterados

- app/apps/api/views.py
- app/apps/dashboard/desktop_views.py
- app/apps/dashboard/mobile_views.py
- app/apps/dashboard/tests/test_mobile_competencia.py
- app/apps/dashboard/tests/test_navigation.py
- app/apps/dashboard/tests/test_post_merge_stability.py
- app/apps/dashboard/urls.py
- app/apps/notifications/models.py
- app/apps/notifications/services.py
- app/apps/notifications/tasks.py
- app/apps/orders/models.py
- app/apps/payments/models.py
- app/apps/webhooks/models.py
- app/apps/webhooks/services.py
- app/apps/webhooks/tasks.py
- app/apps/webhooks/tests.py
- app/apps/webhooks/views.py
- app/apps/webhooks/management/__init__.py
- app/apps/webhooks/management/commands/__init__.py
- app/apps/webhooks/management/commands/reprocess_pagarme_webhook.py
- app/apps/webhooks/migrations/0006_webhookevent_attempt_count_and_more.py
- templates/dashboard/gestor/configuracoes.html
- templates/dashboard/gestor/home.html
- templates/dashboard/gestor/link_detalhe.html
- PAGARME_DIAGNOSTICO_ANTES.md
- PAGARME_RELATORIO_DEPOIS.md

## Migrations

Criada `webhooks.0006_webhookevent_attempt_count_and_more` para status/tentativas/datas de processamento do webhook.

## Evidências dos gates

- `python` local indisponível; usado `.venv/bin/python`.
- `manage.py check`: OK.
- `manage.py makemigrations --check --dry-run`: No changes detected.
- `manage.py migrate --plan`: OK; inclui `webhooks.0006`.
- `manage.py test app.apps.webhooks app.apps.notifications -v 2`: OK, 85 testes.
- Teste focado final: OK, 33 testes.
- `manage.py test -v 2`: executou 759 testes; falhou somente por expectativa antiga ainda esperando `Sale LINK` no reconcile. Expectativa corrigida e validada no teste focado final.
- `manage.py test --parallel 4 -v 1`: OK, 759 testes, 1 skip.
- `check --deploy`: OK com warnings existentes do drf_spectacular e placeholders locais obrigatórios.
- `npm ci`: OK.
- `npm run build:css`: OK.
- `docker compose config`: OK com placeholders locais obrigatórios, warning existente de `version` obsoleto.
- `git diff --check`: OK.
- Grep AGENTS JSON: `grep -rn "json.dumps\|_json.dumps" app/apps/dashboard/*.py | grep -v test` sem saída.

## Observação de escopo

A proposta de `/gestor/webhooks/` como health root-only do sistema é válida, mas deve ser PR separado para não ampliar o Prompt 52.
