# CONTEXTO MERITO

Atualizado em 2026-07-07 para o Prompt 32.

# Estado atual do projeto

O Merito by Vidalys esta na branch base `querolink-v2` em validacao de staging. O Prompt 30 foi estabilizado, billing esta operacional, webhooks estao idempotentes, Correios CWS esta funcionando, embalagens estao configuraveis e o frete foi estabilizado.

O Prompt 32 adiciona somente readiness operacional, observabilidade, documentacao e cobertura CI em PostgreSQL. Nao altera regras de negocio, models, migrations, payloads, APIs publicas, frete, billing, sellers, comissoes, webhooks ou backup.

# Arquitetura atual

- Django 5.1 com Django REST Framework.
- Aplicacao multi-tenant por `Tenant`.
- PostgreSQL em producao.
- SQLite em desenvolvimento e no job legado do CI.
- Redis para broker Celery, result backend e cache em producao.
- Celery worker para tarefas assincronas.
- Celery Beat para tarefas agendadas.
- Templates Django, Alpine.js local e Tailwind compilado.
- Docker Compose no Coolify.
- Sentry opcional com PII desabilitado.

Apps principais:

- `accounts`: tenant, usuarios, onboarding, trial, seguranca e backup.
- `sellers`: vendedores, usuarios vinculados, CPF e metas.
- `sales`: vendas manuais e informativas.
- `commissions`: periodos, ajustes, pagamentos e exportacao contabil.
- `billing`: planos, assinaturas e Mercado Pago.
- `orders` e `payments`: pedidos, links e pagamentos.
- `webhooks`: recebimento, idempotencia, reconcile e limpeza.
- `notifications`: WhatsApp, push, lembretes, e-mails e pacote contabil.
- `freight`: Correios CWS, PAC/SEDEX, fallback estimado e embalagens.
- `dashboard`: gestor desktop e vendedor mobile.
- `api`: endpoints autenticados internos.

# Decisoes tecnicas recentes

- Planos ofertaveis continuam restritos a STARTER, PRO e BUSINESS.
- ENTERPRISE permanece legado e nao deve ser ofertado.
- Dinheiro permanece em centavos.
- Vendas de link continuam informativas; apenas vendas manuais entram em comissao.
- Webhooks usam idempotencia por `gateway_event_id` e falham fechado quando segredo obrigatorio falta.
- Correios CWS usa os codigos oficiais `03220` para SEDEX e `03298` para PAC.
- Fallback de frete deve continuar identificado como estimativa.
- Alpine.js local exige `unsafe-eval` na CSP.
- Segredos de tenant usam `EncryptedCharField`.
- Configuracoes de segredo usam mascara `••••••••` e nao devem sobrescrever valor salvo quando a mascara volta no POST.
- Celery tem time limits e agora observabilidade estruturada via sinais, sem logar args/kwargs das tasks.
- CI passa a validar a suite completa em SQLite e PostgreSQL.

# Estado do staging

Staging esta em validacao funcional. Antes do go-live, ainda e obrigatorio confirmar:

- deploy do PR do Prompt 32;
- todos os checks GitHub Actions verdes;
- `/health/` retornando banco, Redis e Celery operacionais;
- worker Celery saudavel;
- beat ativo;
- backup real no worker;
- restore real em banco PostgreSQL vazio;
- credenciais reais de Pagar.me, Mercado Pago, WhatsApp e Correios CWS.

# Riscos conhecidos

- Restore ainda depende de validacao operacional em banco vazio.
- Integracoes externas nao sao exercitadas de ponta a ponta pela suite automatizada.
- Staging precisa confirmar credenciais reais e conectividade externa.
- Worker/beat precisam ser monitorados apos deploy porque tarefas de backup, reconcile e notificacoes dependem deles.
- Qualquer alteracao em CSP pode quebrar Alpine em producao.
- Qualquer alteracao em nomes de campos de configuracoes pode quebrar persistencia de credenciais.

# Prompt 32

Escopo:

- Observabilidade Celery para tasks criticas.
- Reescrita do `READINESS_REPORT.md`.
- Atualizacao deste `CONTEXTO_MERITO.md`.
- Job GitHub Actions adicional com PostgreSQL.
- Auditoria/minimo ajuste de `/health/`.
- Testes somente para healthchecks, observabilidade e estrutura do readiness report.

Tasks cobertas pela observabilidade:

- `app.apps.webhooks.tasks.process_pagarme_webhook`.
- `app.apps.webhooks.tasks.process_billing_webhook`.
- `app.apps.webhooks.tasks.reconcile_pending_orders`.
- `app.apps.webhooks.tasks.cleanup_old_webhook_events`.
- `app.apps.notifications.tasks.send_whatsapp_notification`.
- `app.apps.notifications.tasks.requeue_stuck_notifications`.
- `app.apps.notifications.tasks.send_daily_entry_reminders`.
- `app.apps.notifications.tasks.send_lifecycle_emails`.
- `app.apps.notifications.tasks.send_accounting_package_email`.
- `app.apps.accounts.tasks.daily_backup`.

Nao ha task de importacao assincrona no codigo atual. As importacoes de sellers/vendas existentes sao endpoints sincronizados e nao foram alteradas.

# Proximos passos recomendados

1. Revisar o PR do Prompt 32 sem merge automatico.
2. Aguardar `backend-sqlite`, `backend-postgres` e `frontend` verdes.
3. Fazer deploy em staging.
4. Validar `/health/`.
5. Confirmar logs estruturados das tasks Celery no worker.
6. Executar backup real.
7. Executar restore em banco vazio.
8. Fazer smoke test das integracoes externas.
9. Autorizar merge em `querolink-v2` somente se nao houver blocker critico.

# Regras que continuam obrigatorias

- Nao criar migrations sem pedido explicito.
- Nao alterar models fora de escopo.
- Nao alterar payloads de integracoes fora de escopo.
- Nao alterar billing, comissoes, sellers, frete, webhooks ou backup fora de escopo.
- Nao remover `unsafe-eval` da CSP.
- Nao usar CDN novo.
- Nao logar tokens, senhas, credenciais, refresh tokens, secrets ou dados pessoais.
- Nao usar `json.dumps()` em dado passado para `json_script`.
- Rodar os gates exigidos antes de entregar.
