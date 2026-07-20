# Deploy em PostgreSQL novo

Este documento é um procedimento para o responsável executar depois do merge. Não contém credenciais reais e não autoriza apagar o banco anterior.

1. Provisionar um PostgreSQL vazio com nome explícito para o ambiente.
2. Configurar `DATABASE_URL` para esse banco.
3. Configurar uma `SECRET_KEY` longa e exclusiva.
4. Configurar `JWT_SIGNING_KEY` longa, obrigatória e diferente da `SECRET_KEY`.
5. Configurar `FERNET_KEY`, Redis, hosts e segredos dos providers necessários.
6. Validar `DEBUG=false` e executar `python manage.py check --deploy`.
7. Executar `python manage.py migrate --plan` e revisar o plano.
8. Executar `python manage.py migrate --noinput`.
9. Criar o primeiro tenant e ADMIN pelo procedimento operacional aprovado.
10. Iniciar o serviço web.
11. Iniciar o worker Celery.
12. Iniciar o Celery Beat.
13. Verificar healthcheck web, PostgreSQL e Redis.
14. Executar smoke tests de ADMIN, MANAGER, FINANCEIRO e SELLER: login, link, boleto, histórico, allocation autorizada e negações.
15. Observar logs de web, worker, beat, outbox, reminder e webhooks.
16. Liberar acesso somente após os smoke tests.

Comandos de referência, após exportar as variáveis corretas:

```bash
python manage.py check --deploy
python manage.py migrate --plan
python manage.py migrate --noinput
python manage.py check
```

Não usar `--fake`, não editar `django_migrations` e não executar DDL de autocura.
