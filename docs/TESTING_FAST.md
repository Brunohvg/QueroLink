# Ambiente rápido e isolado de testes

Este ambiente usa PostgreSQL e Redis descartáveis, separados dos serviços de
desenvolvimento e produção. As portas padrão são `55432` e `56379`.

## Segurança

- O banco deve estar em `localhost`, `127.0.0.1` ou no serviço `test-postgres`.
- O nome do banco e o nome do banco criado pelo Django devem conter `test`.
- E-mail usa o backend em memória.
- Tasks Celery são executadas em modo eager.
- O endpoint de WhatsApp aponta para uma porta local inválida.
- O PostgreSQL e o Redis usam `tmpfs`; `make test-env-down` remove o ambiente.

## Ciclo rápido

Suba os serviços e valide a configuração:

```bash
make test-env-up
make test-check
```

Durante o desenvolvimento, execute somente os módulos afetados:

```bash
make test-affected TESTS="app.apps.accounts.tests"
```

O banco de teste é preservado entre execuções com `--keepdb`. Antes de entregar,
execute obrigatoriamente a suíte completa:

```bash
make test-full
```

Para destruir todos os dados locais do ambiente:

```bash
make test-env-down
```

## Variáveis opcionais

- `TEST_PROCESSES`: quantidade de processos; padrão `auto`.
- `TEST_POSTGRES_PORT`: porta local do PostgreSQL; padrão `55432`.
- `TEST_REDIS_PORT`: porta local do Redis; padrão `56379`.
- `TEST_DATABASE_URL`: aceita somente host local e banco contendo `test`.
- `PYTHON_BIN`: interpretador Python; padrão `.venv/bin/python`.

Este fluxo acelera iterações locais, mas não substitui a suíte completa e os
demais gates obrigatórios do `AGENTS.md`.
