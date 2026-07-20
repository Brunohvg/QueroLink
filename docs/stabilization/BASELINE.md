# Baseline de estabilização

Data: 20/07/2026 — America/Sao_Paulo.

## Git

- Repositório: `Brunohvg/QueroLink`.
- Branch base atualizada: `origin/querolink-v2`.
- SHA inicial: `ec78ea48917889fbbacb3c971fdb67e68f7d0e8c`.
- Branch de trabalho: `fix/stabilize-querolink-v2-fresh-database`.
- `origin/main`: `9c45237bfa1bbc54252c72d697a0f3b0736437c4`.
- Merge-base: `9c45237bfa1bbc54252c72d697a0f3b0736437c4`.
- Distância: 76 commits à frente, 0 atrás.
- Worktree antes da criação da branch: limpo. A auditoria anterior foi preservada fora do repositório.

## Inventário

- 16 apps de projeto instalados.
- 88 migrations de projeto: accounts 24, analytics 2, audit 2, billing 3, commissions 7, customers 2, freight 1, notifications 7, orders 3, payments 2, receivables 8, sales 7, sellers 13 e webhooks 6.
- 67 arquivos de teste e 943 testes descobertos pelo runner.
- PostgreSQL/Redis descartáveis: projeto Compose `querolink-test`.
- Banco exclusivo desta estabilização: `querolink_v2_stabilization_test`.

## Gates no SHA inicial

| Gate | Resultado inicial |
|---|---|
| `manage.py check` | limpo |
| `makemigrations --check --dry-run` | `No changes detected`; primeira tentativa avisou que o banco nomeado ainda não existia |
| `migrate --plan` em PostgreSQL vazio | plano completo gerado; inclui `SeparateDatabaseAndState` em `receivables.0005` e RunPython reparador em `0008` |
| suíte serial | 943 testes; 5 falhas de feature flag/receivables |
| suíte paralela | aborta após falha real porque traceback não é serializável sem `tblib` |
| container local `web` | observado como unhealthy na pré-auditoria; não usado como prova do novo banco |

Falhas iniciais:

1. `ReceivablesFeatureFlagTests.test_eligible_plan_with_disabled_flag_has_no_access`;
2. `ReceivablesFeatureFlagTests.test_flag_is_isolated_by_tenant`;
3. `ReceivablesAPITests.test_create_boleto_unauthorized_when_disabled`;
4. `ReceivablesAPITests.test_feature_disabled_returns_403`;
5. `GestorBoletoViewsTest.test_list_page_redirects_when_disabled`.

## Restrições

Nenhum banco externo será apagado. Não serão usados `--fake`, edição de `django_migrations` ou migrations de autocura. Apenas `receivables` e `customers`, inéditos e introduzidos nesta branch, são candidatos a reconstrução. `accounts.0024` é um `AddField` normal e será mantido.
