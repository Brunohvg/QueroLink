# Estado atual

O Merito by Vidalys esta em validacao de staging na branch `querolink-v2`, apos estabilizacao de billing, webhooks idempotentes, Correios CWS, calculo de frete e embalagens.

Arquitetura:

- Aplicacao Django 5 com Django REST Framework.
- Multi-tenant por `Tenant`, com usuarios ADMIN, MANAGER, SELLER e FINANCIAL.
- Workers Celery e Celery Beat para tarefas assincronas e agendadas.
- Redis como broker Celery e cache em producao.
- PostgreSQL em producao, desenvolvimento e testes automatizados.
- Frontend server-rendered com templates Django, Alpine.js local e Tailwind compilado.
- Deploy por Docker Compose no Coolify.

Stack:

- Python 3.12.
- Django 5.1.
- DRF.
- Celery 5.4.
- Redis 7.
- PostgreSQL.
- Tailwind CSS.
- Pagar.me para links de pagamento.
- Mercado Pago para billing.
- Evolution API para WhatsApp.
- Sentry opcional com PII desabilitado.

Modulos:

- `accounts`: tenants, usuarios, onboarding, seguranca e backup.
- `sellers`: vendedores, CPF, metas e vinculo com usuario.
- `sales`: vendas manuais e vendas informativas de link.
- `commissions`: periodos, calculos, ajustes e exportacao contabil.
- `billing`: assinatura, planos, Mercado Pago e limites.
- `orders`/`payments`: pedidos, links Pagar.me e pagamentos.
- `webhooks`: recebimento, idempotencia, reconcile e limpeza.
- `notifications`: WhatsApp, push, lembretes, e-mails de ciclo de vida e pacote contabil.
- `freight`: cotacao Correios CWS oficial com fallback estimado.
- `dashboard`: telas desktop do gestor e mobile do vendedor.
- `api`: endpoints autenticados internos.

# Funcionalidades prontas

Autenticacao:

- Login por sessao no dashboard.
- JWT/token para APIs autenticadas.
- Reset de senha com rate limit.
- Roles ADMIN, MANAGER, SELLER e FINANCIAL.

Multi-tenant:

- Dados vinculados a tenant.
- Usuarios e vendedores isolados por tenant.
- CSP e middleware de bloqueio operacional por tenant.

Billing:

- Planos ofertaveis STARTER, PRO e BUSINESS.
- Trial, assinatura, upgrade e status operacional.
- Webhook Mercado Pago com HMAC e idempotencia.
- Falha fechada quando segredo obrigatorio nao esta configurado.

Vendedores:

- Cadastro individual.
- Importacao CSV/XLSX com limite por plano.
- CPF normalizado e unico por tenant.
- Convite por WhatsApp sem expor senha em JSON de resposta.

Comissao:

- Vendas manuais entram em comissao.
- Vendas de link sao informativas.
- Periodos, ajustes, pagamento, previa e exportacao contabil.

Dashboard:

- Gestor com metricas, vendas, vendedores, comissoes, assinatura, configuracoes e contabilidade.
- Mobile do vendedor com lancamento, ranking, metas, notificacoes e frete.

Correios:

- Cotacao oficial Correios CWS.
- Servicos PAC e SEDEX mapeados por codigo.
- Embalagens configuraveis.
- Fallback estimado identificado como estimativa.

Notificacoes:

- WhatsApp por tenant ou instancia compartilhada opcional.
- Retry controlado.
- Requeue de notificacoes travadas.
- Push web.
- Lembretes diarios e e-mails de ciclo de vida.

Backup:

- Backup diario via Celery Beat.
- `pg_dump -Fc`.
- Upload para Google Drive via rclone.
- Script de restore documentado.

# Segurança

Hardenings implementados:

- Segredos sensiveis em `EncryptedCharField`.
- Hash para dados sensiveis pesquisaveis quando necessario.
- Webhook Mercado Pago com assinatura HMAC.
- Webhook Pagar.me com Basic Auth opcional por tenant.
- Idempotencia de webhooks por `gateway_event_id`.
- Eventos externos ignorados de forma controlada e processados sem retry infinito.
- `SECRET_KEY`, `DATABASE_URL`, `FERNET_KEY` e credenciais via ambiente.
- CSP customizada preservando `unsafe-eval` para Alpine local.
- Sem CDN novo nas telas.
- `SECURE_SSL_REDIRECT`, HSTS, cookies seguros e `SECURE_PROXY_SSL_HEADER` em producao.
- `SECURE_CONTENT_TYPE_NOSNIFF` e `SECURE_REFERRER_POLICY`.
- Sentry com `send_default_pii=False`.
- Rate limit em reset de senha.
- Validacoes de limite por plano em cadastro/importacao de vendedores.
- Celery com time limits nas tasks criticas.
- Cleanup de webhooks antigos em lotes.
- Backup sem logar credenciais.
- Observabilidade Celery estruturada para inicio, sucesso, duracao e falha das tasks criticas, sem argumentos ou kwargs.

# Testes

Quantidade atual:

- 840 testes automatizados executados no ambiente PostgreSQL isolado.
- O CI executa a suite completa em PostgreSQL, o mesmo banco usado em producao.

Cobertura conhecida:

- Billing, webhooks, limites de plano e trial.
- Sellers, CPF, importacao e exclusao.
- Comissoes, periodos, exportacao e telas relacionadas.
- Notificacoes, retries e push.
- Backup e parse de `DATABASE_URL`.
- Frete, Correios CWS, fallback e renderizacao mobile.
- Configuracoes do gestor.
- Healthchecks operacionais.
- Observabilidade Celery.
- Estrutura obrigatoria deste relatorio.

Limitacoes:

- Testes nao validam o envio real de WhatsApp, e-mail, Pagar.me, Mercado Pago, Correios ou Google Drive.
- Testes nao substituem validacao manual de staging com credenciais reais.
- Restore precisa ser validado operacionalmente em banco vazio antes de go-live.

# Infraestrutura

Docker:

- `web`: Django/Gunicorn.
- `querolink-redis`: Redis com healthcheck.
- `celery_worker`: worker Celery com healthcheck por `celery inspect ping`.
- `celery_beat`: agendador Celery.
- Volumes para media, Redis, backups e schedule do beat.

Coolify:

- Deploy por Docker Compose.
- Rede externa `coolify`.
- Variaveis sensiveis via ambiente.
- `/health/` liberado de redirect SSL.

Redis:

- Broker Celery.
- Result backend Celery.
- Cache em producao.
- Validado pelo healthcheck da aplicacao quando backend Redis esta ativo.

Celery:

- Worker separado do web.
- Beat separado.
- Time limits por task.
- Observabilidade por sinais Celery em tasks criticas.
- Healthcheck do container do worker via `inspect ping`.
- Healthcheck HTTP valida conectividade com broker.

GitHub Actions:

- `backend-postgres`: check, deploy check, migrations e suite em PostgreSQL.
- `frontend`: install e build CSS.
- Jobs independentes.

Backup:

- Agendado diariamente as 02:00.
- Gera dump PostgreSQL em formato custom.
- Envia para Google Drive com rclone.
- Mantem retencao local/remota configuravel.

Restore:

- Restore por `scripts/restore.sh`.
- Dumps `.dump` sao restaurados com `pg_restore`.
- Validacao em banco vazio continua obrigatoria antes de producao.

# Pendências

- Validar staging ponta a ponta apos merge do Prompt 32.
- Validar backup real no worker com rclone configurado.
- Validar restore em banco PostgreSQL vazio.
- Confirmar GitHub Actions verdes no Pull Request do Prompt 32.
- Confirmar `/health/` em staging retornando banco, Redis e Celery como operacionais.

# Checklist Produção

- [ ] Branch do Prompt 32 com PR aprovado.
- [ ] `backend-postgres` verde.
- [ ] `frontend` verde.
- [ ] `python manage.py check` limpo.
- [ ] `DJANGO_SETTINGS_MODULE=app.config.settings.production python manage.py check --deploy` revisado.
- [ ] `python manage.py makemigrations --check --dry-run` sem migrations pendentes.
- [ ] `docker compose config` valido.
- [ ] Variaveis obrigatorias configuradas no Coolify.
- [ ] `FERNET_KEY` configurada e preservada.
- [ ] `MP_WEBHOOK_SECRET` configurado.
- [ ] Credenciais Pagar.me por tenant validadas.
- [ ] Credenciais Correios CWS validadas.
- [ ] Evolution API/WhatsApp validado.
- [ ] Backup real gerado.
- [ ] Restore real testado em banco vazio.
- [ ] `/health/` retorna `status=ok` em staging.
- [ ] Worker Celery saudavel.
- [ ] Beat Celery ativo.
- [ ] Sentry configurado ou decisao registrada de operar sem DSN.
- [ ] Go-live autorizado.
