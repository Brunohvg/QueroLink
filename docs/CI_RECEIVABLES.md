# CI de Recebíveis — PostgreSQL + Redis

## Pipeline

O CI é definido em `.github/workflows/ci.yml` com três jobs paralelos:

### `backend-postgres`

Serviços: PostgreSQL 17 + Redis 7.

Gates executados em ordem:

| # | Gate | O que verifica |
|---|---|---|
| 1 | `python manage.py check` | Validade da configuração Django |
| 2 | `python manage.py check --deploy` | Segurança em produção (`settings.production`) |
| 3 | `makemigrations --check --dry-run` | Nenhuma migration não commitada |
| 4 | `migrate --plan` | Plano de migrations é válido |
| 5 | Teste completo (`--parallel`) | Suíte completa em PostgreSQL |

Falha em qualquer gate = workflow bloqueado.

### `frontend`

Assets CSS via Tailwind.

| Gate | Comando |
|---|---|
| Instalar deps | `npm ci` (usando lockfile) |
| Build CSS | `npm run build:css` |

### `config-and-scripts`

Validação de configuração e scripts shell.

| Gate | Comando |
|---|---|
| Docker Compose | `docker compose config` |
| Shell syntax | `bash -n scripts/*.sh` |

## Checks obrigatórios na proteção de branch

Para configurar no GitHub (Settings → Branches → Branch protection rule para `querolink-v2`):

Marcar como **Required**:
- `backend-postgres`
- `frontend`
- `config-and-scripts`

Recomendado adicional:
- ☐ `Require pull request reviews before merging`
- ☐ `Dismiss stale pull request approvals when new commits are pushed`
- ☐ `Require branches to be up to date`
- ☐ `Do not allow bypassing the above settings`

## Variáveis de ambiente do CI

Todas configuradas no workflow. Nenhum segredo real é usado:
- `SECRET_KEY`: `ci-test-secret-key-not-for-production`
- `FERNET_KEY`: chave fixa de 44 bytes para deploy check
- `DATABASE_URL`: aponta para o service `postgres` do próprio job
- `REDIS_URL`: aponta para o service `redis`

## Manutenção

- Para adicionar um gate: adicione um step no job `backend-postgres`
- Para mudar versão do Python: altere `python-version` no `setup-python`
- Cache pip e npm são automáticos via `actions/setup-python` e `actions/setup-node`
