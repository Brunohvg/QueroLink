# Relatório de testes da estabilização

Data de referência: 20/07/2026, America/Sao_Paulo.

## Baseline

- 943 testes descobertos;
- suíte serial: 5 falhas, todas ligadas à neutralização do flag de boletos;
- suíte paralela: bloqueada pela ausência de `tblib` antes de exibir corretamente a primeira falha;
- `check`: limpo;
- `makemigrations --check`: limpo.

## Evidências intermediárias

- 26 testes focados de feature/API/views: OK;
- 26 testes de API e notifications, incluindo envio/falha/retry: OK;
- 6 testes de Central/outbox: OK;
- 10 testes de XLSX e autocomplete CNPJ/CEP: OK;
- primeiro rerun paralelo após a correção: 947 testes, 1 falha de expectativa legada do reminder; o teste foi atualizado para mockar confirmação real do canal.

## PostgreSQL novo

- PostgreSQL 17 em container local descartável;
- banco `querolink_v2_stabilization_test`, criado especificamente para esta estabilização;
- migrate inicial completo: OK;
- migrate repetido: no-op;
- nenhum SQLite usado como prova.

## Configuração

- ausência de `JWT_SIGNING_KEY` em production: falha explícita com `ImproperlyConfigured`;
- production `check --deploy` com JWT distinto: exit 0;
- warnings existentes do schema OpenAPI permanecem documentados e não foram ampliados neste PR.

## Gates finais

- testes serial explícito (`--parallel=1`): 958, 86,690 s, 0 failures, 0 errors;
- testes padrão: 958, 109,482 s, 0 failures, 0 errors;
- testes paralelos: 958, 52,422 s, 0 failures, 0 errors;
- `python manage.py check`: 0 issues;
- `makemigrations --check --dry-run`: `No changes detected`;
- `migrate --plan` no banco migrado: `No planned migration operations`;
- `migrate --noinput` repetido: `No migrations to apply`;
- production `check --deploy`: exit 0, com 33 warnings preexistentes do gerador OpenAPI;
- `npm run build:css`: OK; warning de base Browserslist desatualizada;
- `docker compose config -q`: OK; warning de atributo `version` obsoleto;
- `git diff --check`: OK;
- grep obrigatório de `json.dumps` em dashboard: 0 ocorrências;
- query service da Central com um link e um boleto: 3 queries, coberto por `assertNumQueries`.

Os testes foram preservados com `--keepdb` para cumprir a proibição de executar `DROP DATABASE` automaticamente. O banco e clones são exclusivamente locais, descartáveis e contêm `test` no nome.

## Arquivos da entrega

Foram alterados 60 paths, todos dentro do escopo de estabilização:

```text
.env.example
.github/workflows/ci.yml
app/apps/accounts/models.py
app/apps/accounts/plans.py
app/apps/api/tests/test_csv_safety.py
app/apps/api/tests/test_sales_matrix.py
app/apps/api/views.py
app/apps/commissions/exports.py
app/apps/customers/migrations/0001_initial.py
app/apps/customers/migrations/0002_customer_sources_conflicts.py (removido)
app/apps/dashboard/charge_center.py
app/apps/dashboard/desktop_views.py
app/apps/dashboard/mobile_views.py
app/apps/dashboard/tests/test_charge_center.py
app/apps/dashboard/views.py
app/apps/receivables/api.py
app/apps/receivables/migrations/0001_initial.py
app/apps/receivables/migrations/0002_receivable_outbox.py (removido)
app/apps/receivables/migrations/0003_receivable_allocation.py (removido)
app/apps/receivables/migrations/0004_commission_impact_review.py (removido)
app/apps/receivables/migrations/0005_boleto_invoice_files.py (removido)
app/apps/receivables/migrations/0006_boleto_delivery_fields.py (removido)
app/apps/receivables/migrations/0007_receivable_notification_delivery.py (removido)
app/apps/receivables/migrations/0008_boleto_provider_column.py (removido)
app/apps/receivables/tasks.py
app/apps/receivables/tests/test_api.py
app/apps/receivables/tests/test_notifications.py
app/apps/receivables/tests/test_outbox_consumers.py
app/apps/receivables/tests/test_views.py
app/apps/receivables/views.py
app/apps/sales/services_matrix.py
app/config/settings/production.py
app/services/csv_safety.py
docs/stabilization/BASELINE.md
docs/stabilization/CANONICAL_SCHEMA.md
docs/stabilization/DEPLOY_NEW_DATABASE.md
docs/stabilization/FEATURE_POLICY.md
docs/stabilization/IMPORT_STATUS.md
docs/stabilization/MIGRATION_PLAN.md
docs/stabilization/PERMISSION_MATRIX.md
docs/stabilization/PR_AUDIT.md
docs/stabilization/REGRESSION_MATRIX.md
docs/stabilization/TEST_REPORT.md
requirements/base.txt
static/js/json-script.js
templates/accounts/signup.html
templates/base/base.html
templates/dashboard/base_desktop.html
templates/dashboard/gestor/boletos/detail.html
templates/dashboard/gestor/boletos/list.html
templates/dashboard/gestor/boletos/new.html
templates/dashboard/gestor/cobrancas.html
templates/dashboard/gestor/configuracoes.html
templates/dashboard/gestor/links.html
templates/dashboard/login.html
templates/mobile/base_mobile.html
templates/mobile/boletos/list.html
templates/mobile/cobrancas.html
templates/mobile/frete.html
templates/mobile/links.html
```
