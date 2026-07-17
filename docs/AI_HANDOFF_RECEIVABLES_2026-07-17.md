# Handoff técnico — Mérito Cobranças e Recebíveis

Atualizado em 17/07/2026. Este documento permite que outra IA ou pessoa continue o trabalho sem depender do histórico desta conversa.

## 1. Regras obrigatórias antes de continuar

1. Ler integralmente `AGENTS.md`.
2. Trabalhar somente um prompt/PR por vez.
3. Toda branch nova nasce da `origin/querolink-v2` atualizada após o merge anterior.
4. Nunca fazer merge automático, deploy ou alteração em produção.
5. A branch `prompt-42-boletos` e o commit `ddf541b...` são somente referência; não fazer merge/cherry-pick integral.
6. Alterar somente os arquivos autorizados pelo prompt. Se outro arquivo for indispensável, parar e pedir autorização.
7. Executar a suíte completa em PostgreSQL antes de entregar.
8. Não reordenar nem substituir migrations existentes. Este ponto é especialmente crítico devido ao incidente descrito na seção 8.

## 2. Estado atual

- Repositório: `Brunohvg/QueroLink`.
- Branch de integração: `querolink-v2`.
- HEAD mesclado usado como base: `af067da56eac8a4c59194768efa771740bc5ab6a` (PR #43).
- Branch em desenvolvimento: `feat/customer-ledger-foundation`.
- Prompt em entrega: **Prompt 08 — Customer Ledger como projeção assíncrona**.
- Estado local: implementação concluída e gates locais verdes; falta publicar o Draft PR e registrar abaixo seu número/commit final.
- Próximo prompt, somente depois do merge verde deste PR: **Prompt 09 — API do Customer Ledger e backfill controlado**.

Antes de iniciar o Prompt 09, confirmar o estado do Draft PR do Prompt 08 que será registrado neste documento:

```bash
gh pr view NUMERO_DO_PR_08 --json state,mergedAt,statusCheckRollup
git fetch origin --prune
```

## 3. PRs executados

| PR | Conteúdo | Estado | Merge commit |
|---|---|---|---|
| #35 | Ambiente rápido isolado PostgreSQL/Redis | Mesclado | `18fa25d` |
| #36 | Prompt 01 — fundação e feature flag | Mesclado | `c5f84a6` |
| #37 | Prompt 02 — provider e emissão idempotente | Mesclado | `9eb0022` |
| #38 | Prompt 03 — ciclo financeiro e outbox | Mesclado | `d501127` |
| #39 | Prompt 04 — roteamento de webhooks | Mesclado | `75473dd` |
| #40 | Padronização PostgreSQL; remoção do gate SQLite | Mesclado | `3bb9608` |
| #41 | Prompt 05 — reconciliação de boletos | Mesclado | `f9d8c28` |
| #42 | Prompt 06 — alocação em vendas | Mesclado | `e592820` |
| #43 | Prompt 07 — impacto em comissões | Mesclado | `af067da` |
| a registrar | Prompt 08 — Customer Ledger assíncrono | Implementação local verde | branch `feat/customer-ledger-foundation` |

## 4. Decisões de arquitetura tomadas

- PostgreSQL é o único banco suportado em desenvolvimento, testes e produção.
- SQLite foi removido do CI; migrations históricas e ignores antigos foram preservados.
- Feature de recebíveis exige direito do plano **e** `tenant.receivables_enabled=True`.
- Pagamento/cancelamento/estorno usam transações curtas e outbox durável.
- Chamadas ao provider nunca ficam dentro de `transaction.atomic`.
- Webhooks de boleto só são reconhecidos por correlação local tenant-scoped; `payment_method=boleto` isolado não basta.
- Reconciliação de boletos é separada de `reconcile_pending_orders`.
- Um boleto `PAGO` sem `ReceivableAllocation` representa o estado derivado “aguardando alocação”. Não existe allocation PENDING.
- A alocação explícita cria/atualiza a Sale MANUAL do vendedor e dia usando exclusivamente `paid_amount_cents` e `paid_at`.
- Comissão aberta/reaberta é recalculada.
- Comissão fechada/ajustada/paga não tem valores congelados/pagos alterados automaticamente; gera `CommissionImpactReview PENDING`.
- Aprovação explícita usa `CommissionAdjustment`; comissão PAGA exige compensação futura e não é ajustada automaticamente.
- Customer Ledger é projeção assíncrona dos eventos duráveis de boleto e mantém transação própria.
- A outbox contém somente UUID do boleto; PII é buscada dentro da task e armazenada criptografada.
- Documento é identidade forte por tenant. Nome nunca identifica; e-mail e telefone só reutilizam uma correspondência única sem identidade forte conflitante.
- Divergências entre documento, e-mail ou telefone criam `CustomerIdentityConflict`; nunca há merge automático silencioso.
- `CustomerActivity` é idempotente por tenant + origem + UUID da origem.

## 5. Ambiente de teste oficial

```bash
./scripts/test-fast.sh up
./scripts/test-fast.sh check
./scripts/test-fast.sh run app.apps.modulo.tests
./scripts/test-fast.sh full
./scripts/test-fast.sh down
```

O ambiente valida host local e nome de banco contendo `test`. No Prompt 08, 24 testes focados/regressivos passaram e a suíte completa passou com **878 testes em 48,963s**.

## 6. Como continuar no Prompt 09

Após o Draft PR do Prompt 08 estar verde e mesclado:

```bash
git fetch origin --prune
git switch -c feat/customer-ledger-api origin/querolink-v2
git status -sb
```

Ler novamente o Prompt 09 no arquivo original anexado à sessão. Escopo resumido:

- expor a API do Customer Ledger com paginação e isolamento por tenant;
- implementar consentimento operacional e de marketing conforme o escopo exato;
- criar backfill explícito e idempotente, nunca executado em migration ou boot;
- preservar integralmente a política de identidade criada no Prompt 08;
- alterar somente os arquivos autorizados pelo Prompt 09.

Antes de editar, reler integralmente a seção do Prompt 09, listar os arquivos autorizados e confirmar que nenhum arquivo extra será necessário.

### Evidências locais do Prompt 08

- `python manage.py check`: sem problemas.
- `makemigrations --check --dry-run`: `No changes detected`.
- `migrate --plan`: inclui `customers.0001_initial` depois de `accounts.0024`, criando os três modelos e somente as duas constraints autorizadas.
- Testes focados/regressivos: 24 testes, todos verdes.
- Suíte completa PostgreSQL: 878 testes, todos verdes.
- `git diff --check`: limpo.

## 7. Fluxo de entrega de cada prompt

1. Confirmar merge e gates do PR anterior.
2. Atualizar `origin/querolink-v2`.
3. Criar a branch exata do prompt.
4. Implementar apenas o escopo permitido.
5. Rodar testes focados em PostgreSQL.
6. Rodar:

```bash
./scripts/test-fast.sh check
DJANGO_SETTINGS_MODULE=app.config.settings.test_fast DEBUG=False \
  TEST_DATABASE_URL=postgresql://querolink_test@127.0.0.1:55432/querolink_test \
  TEST_REDIS_URL=redis://127.0.0.1:56379/0 \
  .venv/bin/python manage.py makemigrations --check --dry-run
./scripts/test-fast.sh full
```

7. Se houver migration, executar e registrar `migrate --plan`.
8. Conferir `git diff --check` e a lista exata de arquivos.
9. Commitar, fazer push da branch e abrir Draft PR contra `querolink-v2`.
10. Não mesclar automaticamente.

## 8. Incidente atual de deploy: histórico inconsistente de migrations

### Sintoma

O container reinicia durante `migrate --noinput` com:

```text
InconsistentMigrationHistory: Migration receivables.0001_initial is applied
before its dependency accounts.0024_tenant_receivables_enabled
```

O warning `JWT_SIGNING_KEY nao configurada` não causa o crash. Ele indica fallback para `SECRET_KEY` e deve ser tratado depois como configuração de segurança separada.

### Causa confirmada

Uma versão antiga de `receivables.0001_initial` (commit histórico `49a261b`) dependia de `accounts.0023` e foi aplicada no banco de deploy. A versão atual do mesmo arquivo migration passou a depender de `accounts.0024`. Assim, o banco registra `receivables.0001` aplicada, mas não registra `accounts.0024`; o loader do Django bloqueia qualquer comando `migrate` antes de executar operações.

Isso não pode ser corrigido por uma nova migration normal, porque o Django falha antes de chegar nela. Também não se deve editar novamente a dependency de `0001`.

### Recuperação operacional segura

**Não executar sem backup PostgreSQL e janela controlada.** Primeiro parar tentativas concorrentes de deploy/entrypoint.

1. Fazer backup `pg_dump -Fc` e guardar o arquivo fora do container.
2. Conectar ao PostgreSQL correto e executar apenas as consultas de diagnóstico:

```sql
SELECT app, name, applied
FROM django_migrations
WHERE (app = 'accounts' AND name IN ('0023_tenant_period_start_day', '0024_tenant_receivables_enabled'))
   OR (app = 'receivables' AND name = '0001_initial')
ORDER BY applied;

SELECT column_name, data_type, is_nullable, column_default
FROM information_schema.columns
WHERE table_name = 'accounts_tenant'
  AND column_name = 'receivables_enabled';
```

Pré-condição esperada para este reparo:

- `accounts.0023` aplicada;
- `receivables.0001` aplicada;
- `accounts.0024` ausente.

3. Se qualquer pré-condição divergir, **parar** e reavaliar. Não executar `--fake` às cegas.
4. Se a coluna `receivables_enabled` estiver ausente, executar em uma transação:

```sql
BEGIN;
ALTER TABLE accounts_tenant
  ADD COLUMN IF NOT EXISTS receivables_enabled boolean NOT NULL DEFAULT false;
ALTER TABLE accounts_tenant
  ALTER COLUMN receivables_enabled DROP DEFAULT;
INSERT INTO django_migrations (app, name, applied)
SELECT 'accounts', '0024_tenant_receivables_enabled', NOW()
WHERE NOT EXISTS (
  SELECT 1 FROM django_migrations
  WHERE app = 'accounts' AND name = '0024_tenant_receivables_enabled'
);
COMMIT;
```

5. Se a coluna já existir com tipo boolean e `NOT NULL`, não recriá-la; registrar somente a migration dentro de transação:

```sql
BEGIN;
INSERT INTO django_migrations (app, name, applied)
SELECT 'accounts', '0024_tenant_receivables_enabled', NOW()
WHERE NOT EXISTS (
  SELECT 1 FROM django_migrations
  WHERE app = 'accounts' AND name = '0024_tenant_receivables_enabled'
);
COMMIT;
```

6. Validar antes de reiniciar o deploy:

```bash
python manage.py showmigrations accounts receivables
python manage.py migrate --plan
python manage.py migrate --noinput
python manage.py check
```

7. Confirmar que todos os tenants ficaram com `receivables_enabled=false` salvo ativação explícita posterior.

### Rollback

Se uma instrução SQL falhar, a transação deve ser revertida automaticamente. Se o deploy continuar inconsistente, não apagar linhas de `django_migrations`; restaurar o backup em banco separado para investigação e manter produção no último release funcional.

## 9. JWT_SIGNING_KEY

O warning indica que JWTs estão sendo assinados com `SECRET_KEY`. Não é a causa da queda, mas o recomendado é configurar no ambiente de produção uma `JWT_SIGNING_KEY` aleatória, longa e independente. Não trocar essa chave durante o incidente de migration sem planejar a invalidação dos tokens JWT existentes.

## 10. Estado do documento

Este arquivo é documentação operacional contínua. O usuário autorizou explicitamente em 17/07/2026 que ele seja atualizado em todas as etapas para permitir que outra IA retome o trabalho. A partir do Prompt 08, toda entrega deve registrar aqui: PR/commit, gates, decisões, pendências e próximo ponto exato de retomada.
