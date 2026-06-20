# PRD — QueroLink Sistema de Comissões

> **Status geral:** MVP completo — Lotes 1 a 5 implementados. 55 testes passando.
> **Versão:** 1.0.0-MVP | **Última atualização:** 2026-06-20
> **Recomendação:** ✅ PRONTO PARA STAGING (ver `READINESS_REPORT.md`)

---

## 1. Visão Geral

QueroLink é um sistema multi-tenant de gestão de comissões para vendedores de lojas.
O tenant principal é a Loja Bibelô, com 16 vendedores reais.

## 2. Arquitetura

- Django 5.1 + SQLite (dev) / PostgreSQL (prod)
- Celery + Redis para tarefas assíncronas (envio de WhatsApp via Evolution API)
- Multi-tenant via model Tenant, com isolamento lógico nos models
- Custom User model com roles: ADMIN, MANAGER, FINANCEIRO, SELLER
- Integração WhatsApp: Evolution API (`api.lojabibelo.com.br`), client em `app/services/messaging/whatsapp.py`
- Dependência real: `requirements/base.txt` + `requirements/production.txt` (Dockerfile)

## 3. Apps

| App | Função | Status |
|-----|--------|--------|
| accounts | Tenant, User customizado | ✅ Lote 1 |
| sellers | Seller (vendedor) | ✅ Lote 1 + 1.5 |
| orders | Order, PaymentLink | Existente (fora de escopo) |
| payments | Payment (transações gateway) | Existente (fora de escopo) |
| sales | Sales (vendas lançadas) | ✅ Cross-tenant validation adicionada |
| commissions | CommissionPeriod, SellerCommission | Existente |
| webhooks | WebhookEvent (Pagar.me) | Bug conhecido, fora de escopo |
| notifications | Templates + envio WhatsApp | ✅ Lote 1.5 (generalizado) |
| analytics | Analytics de cliques | Não implementado |
| audit | Auditoria | Não implementado |
| dashboard | Dashboard do gestor + cadastro de vendedor | ✅ Lote 1.5 (tela seller create) |
| api | API REST | ❌ Lote 2 (futuro) |

---

## 4. Models

### 4.1 Seller (app `sellers`)

```python
class Seller(models.Model):
    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name='sellers')
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='seller_profile')  # Lote 1
    name = models.CharField(max_length=100)
    phone = models.CharField(max_length=20)
    commission_rate = models.DecimalField(max_digits=5, decimal_places=4, default=0.01)                              # Lote 1
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
```

**Related names vindos de outros models → Seller (não usar como `@property`):**
- `sales` (de Sale.seller)
- `orders` (de Order.seller)
- `commissions` (de SellerCommission.seller)

**Migrations Seller (4 arquivos):**
- `0001_initial` — tabela original
- `0002_add_user_and_commission_rate` — +`user` (nullable, `CASCADE`), +`commission_rate`
- `0003_link_sellers_to_users` — data migration `RunPython` (cria User para Sellers órfãos)
- `0004_make_user_required` — `AlterField` remove `null=True`

### 4.2 Tenant (app `accounts`)

```python
class Tenant(models.Model):
    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company_name = models.CharField(max_length=255)
    cnpj = models.CharField(max_length=14, unique=True, blank=True, null=True)
    pagarme_api_key = models.CharField(max_length=255, blank=True, null=True)
    whatsapp_instance_id = models.CharField(max_length=100, blank=True, null=True)
    whatsapp_token = models.CharField(max_length=255, blank=True, null=True)
    default_commission_rate = models.DecimalField(max_digits=5, decimal_places=4, default=0.01)  # Lote 1
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
```

**Migration Accounts:**
- `0002_add_user_and_commission_rate` — +`default_commission_rate`

### 4.3 User (app `accounts`)

```python
class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = 'ADMIN', 'Admin'
        MANAGER = 'MANAGER', 'Manager'
        FINANCEIRO = 'FINANCEIRO', 'Financeiro'
        SELLER = 'SELLER', 'Vendedor'

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, null=True, blank=True, related_name='users')
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.MANAGER)
```

### 4.4 Sale (app `sales`) — Cross-tenant validation

Adicionado em `clean()` (Lote 1):
```python
if self.seller.tenant_id != self.tenant_id:
    raise ValidationError("O vendedor nao pertence ao tenant da venda.")
```

### 4.5 Notification + MessageTemplate (app `notifications`) — Lote 1.5

#### MessageTemplate

```python
class MessageTemplate(models.Model):
    class EventType(models.TextChoices):
        LINK_CREATED = 'link_created', 'Link Created'
        LINK_OPENED = 'link_opened', 'Link Opened'
        CHECKOUT_STARTED = 'checkout_started', 'Checkout Started'
        PAYMENT_PAID = 'payment_paid', 'Payment Paid'
        PAYMENT_FAILED = 'payment_failed', 'Payment Failed'
        PAYMENT_EXPIRED = 'payment_expired', 'Payment Expired'
        PAYMENT_REFUNDED = 'payment_refunded', 'Payment Refunded'
        PAYMENT_CHARGEBACK = 'payment_chargeback', 'Payment Chargeback'
        SELLER_CREDENTIALS = 'seller_credentials', 'Seller Credentials'   # Lote 1.5
        COMMISSION_PAID = 'commission_paid', 'Commission Paid'            # Lote 1.5
```

**Templates padrão (data migration `0002`):**

- `SELLER_CREDENTIALS` (whatsapp):
  `"Ola {{vendedor}}! Seu acesso ao sistema de comissoes foi criado.\nUsuario: {{usuario}}\nSenha temporaria: {{senha}}\nAcesse e troque sua senha no primeiro login."`

- `COMMISSION_PAID` (whatsapp):
  `"Ola {{vendedor}}! Sua comissao de {{periodo}} no valor de {{valor}} foi paga. Confira os detalhes no app."`

#### Notification

```python
class Notification(models.Model):
    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, null=True, blank=True, ...)          # Lote 1.5 — auto-preenchido no save()
    order = models.ForeignKey(Order, null=True, blank=True, ...)            # Lote 1.5 — agora nullable
    seller = models.ForeignKey(Seller, null=True, blank=True, ...)          # Lote 1.5 — novo
    commission_period = models.ForeignKey(CommissionPeriod, null=True, ...) # Lote 1.5 — novo
    event_type = models.CharField(choices=MessageTemplate.EventType.choices)
    channel = models.CharField(choices=MessageTemplate.Channel.choices)
    recipient = models.CharField(max_length=255)
    message_body = models.TextField()
    status = models.CharField(choices=(PENDING/SENT/FAILED))
    retry_count = models.PositiveIntegerField(default=0)
    error_log = models.TextField(blank=True, null=True)
```

**Migrations Notifications (2 arquivos):**
- `0001_generalize_notification` — criação inicial dos models
- `0002_create_default_templates` — data migration (templates padrão por tenant)

---

## 5. Comandos de Management

### `reset_seller_password` (Lote 1)

```bash
python manage.py reset_seller_password <seller_uuid>
```

Gera nova senha temporária para o vendedor, aplica via `set_password()`, imprime no terminal.
Cobre "esqueci minha senha" sem e-mail. Local: `sellers/management/commands/reset_seller_password.py`.

---

## 6. Tarefas Assíncronas (Celery)

### `send_whatsapp_notification` (Lote 1.5)

Task Celery em `notifications/tasks.py`. Usa `WhatsappClient` existente (`app/services/messaging/whatsapp.py`).
- Retry: 3 tentativas com backoff exponencial (60s, 120s, 240s)
- Max retries: falha definitiva após `MAX_RETRIES=3`
- Fallback: se não houver `MessageTemplate` cadastrado, usa `_fallback_body()` com texto em português

### `notify_seller_credentials(seller, password)` (Lote 1.5)

Cria `Notification` com `event_type=SELLER_CREDENTIALS`, dispara task assíncrona.
Chamada automaticamente pelo `dashboard.seller_create`.

### `notify_commission_paid(seller_commission)` (Lote 1.5)

Cria `Notification` com `event_type=COMMISSION_PAID`, dispara task assíncrona.
Pronta para ser chamada pelo Lote 2 (API) e Lote 4 (tela financeiro).

---

## 7. Fluxos Implementados

### 7.1 Cadastro de vendedor pelo gestor (Lote 1.5)
- URL: `POST /dashboard/sellers/create/`
- Autenticação: `@login_required` (qualquer role com tenant)
- Form: nome + telefone
- Sistema gera username (`slugify(name)` + sufixo se duplicado) + senha temporária (`get_random_string(12)`)
- Cria `User` com `role=SELLER`, vinculado ao `Seller`
- `commission_rate` inicial = `Tenant.default_commission_rate`
- Dispara `notify_seller_credentials()` (WhatsApp) — **falha no envio NÃO bloqueia a criação**
- Exibe username + senha + status do envio na tela

### 7.2 Onboarding do vendedor (recém-criado)
1. Recebe WhatsApp com username + senha temporária
2. Login via username + senha
3. Troca de senha (futuro: tela de perfil)
4. Acesso às telas de vendedor (Lotes futuros)

### 7.3 Reset de senha (sem e-mail)
- Gestor roda `python manage.py reset_seller_password <uuid>` no servidor
- Senha temporária gerada e impressa no terminal
- Gestor repassa manualmente ao vendedor

---

## 8. Telas

### 8.1 Implementadas

| Tela | URL | Template | Status |
|------|-----|----------|--------|
| Login dashboard (desktop) | `/dashboard/login/` | `dashboard/login.html` | Existente |
| Dashboard home | `/dashboard/` | `dashboard/index.html` | Existente |
| Cadastro vendedor | `/dashboard/sellers/create/` | `dashboard/seller_create.html` | ✅ Lote 1.5 |
| Login mobile | `/dashboard/mobile/login/` | `mobile/login.html` | ✅ Lote 3 |
| Home mobile | `/dashboard/mobile/` | `mobile/home.html` | ✅ Lote 3 |
| Lançar venda | `/dashboard/mobile/lancar/` | `mobile/lancar_venda.html` | ✅ Lote 3 |
| Minhas vendas | `/dashboard/mobile/vendas/` | `mobile/minhas_vendas.html` | ✅ Lote 3 |
| Meu desempenho | `/dashboard/mobile/desempenho/` | `mobile/meu_desempenho.html` | ✅ Lote 3 |
| Esqueci senha | `/dashboard/mobile/forgot-password/` | `mobile/forgot_password.html` | ✅ Lote 3 |
| Link público | `/` | `orders/index.html` | Existente |

### 8.2 PWA (Lote 3)

- `static/manifest.json` — 8 ícones, `display: standalone`, `theme_color: #4361ee`
- `static/sw.js` — cache de assets, fallback offline "Sem conexão"
- Service worker registrado no `base_mobile.html`
- Prompt de instalação via `beforeinstallprompt` — aparece após 2+ logins
- `static/icons/` — 8 PNGs (72 a 512px)

### 8.3 Decisão de frontend mobile

**Alpine.js** escolhido sobre HTMX:
- API REST retorna JSON — Alpine consome nativamente via `fetch()`
- Reatividade local sem build step (`x-model`, `x-show`, `x-for`)
- Máscara monetária em JS (entrada em R$, submit em centavos)
- Sessão Django (cookie) para autenticação — mais seguro que JWT em localStorage para dispositivos compartilhados

### 8.4 Telas desktop — Lote 4

| Tela | URL | Template | Status |
|------|-----|----------|--------|
| Ranking gestor | `/dashboard/gestor/ranking/` | `dashboard/gestor/ranking.html` | ✅ Lote 4 |
| Vendedores gestor | `/dashboard/gestor/vendedores/` | `dashboard/gestor/vendedores.html` | ✅ Lote 4 |
| Fechamento gestor | `/dashboard/gestor/fechamento/` | `dashboard/gestor/fechamento.html` | ✅ Lote 4 |
| Fila aprovação financeiro | `/dashboard/financeiro/fila/` | `dashboard/financeiro/fila_aprovacao.html` | ✅ Lote 4 |
| Histórico pagamentos | `/dashboard/financeiro/historico/` | `dashboard/financeiro/historico_pagamentos.html` | ✅ Lote 4 |

### 8.5 Infraestrutura Tailwind (Lote 4)

- `package.json` + `tailwind.config.js` com paleta curada (Inter, cores primárias)
- Dockerfile multi-stage: `node:20-alpine` (build CSS) → `python:3.12.3-slim` (sem Node na imagem final)
- `static/css/input.css` com `@tailwind` directives + componentes utilitários
- `templates/dashboard/base_desktop.html` — sidebar com menu role-based
- Chart.js 4.4 para gráfico de ranking
- `app.apps.audit` registrado em `INSTALLED_APPS`, migration gerada, logging em approve/reject

---

## 9. API REST (✅ Lote 2 implementado)

### 9.1 Autenticação
- JWT via `djangorestframework-simplejwt`
- `POST /api/auth/login/` — login (rate limit 5/min por IP + username)
- `POST /api/auth/refresh/` — refresh token
- `TokenAuthentication` mantido em paralelo (legacy)
- Variáveis de ambiente: `JWT_ACCESS_TOKEN_LIFETIME_MINUTES` (default 30), `JWT_REFRESH_TOKEN_LIFETIME_DAYS` (default 7)

### 9.2 Endpoints

| Método | URL | Permissão | Descrição |
|--------|-----|-----------|-----------|
| GET/POST | `/api/sellers/` | MANAGER/ADMIN | Listar/criar vendedores |
| GET/PUT/DELETE | `/api/sellers/{uuid}/` | MANAGER/ADMIN | Detalhe/editar/remover vendedor |
| GET/POST | `/api/sales/` | SELLER/MANAGER/ADMIN | Listar/criar vendas |
| GET/PUT/DELETE | `/api/sales/{uuid}/` | SELLER (própria)/MANAGER | Detalhe/editar/remover venda |
| GET | `/api/seller/sales/` | SELLER | Listar vendas do próprio vendedor |
| GET | `/api/manager/sales/` | MANAGER/ADMIN | Listar todas as vendas do tenant (?seller=uuid) |
| GET/POST | `/api/commissions/periods/` | MANAGER/ADMIN | Listar/criar competências |
| POST | `/api/commissions/periods/{uuid}/close/` | MANAGER/ADMIN | Fechar → EM_CONFERENCIA |
| POST | `/api/commissions/periods/{uuid}/send/` | MANAGER/ADMIN | Enviar → ENVIADA_FINANCEIRO |
| POST | `/api/commissions/periods/{uuid}/approve/` | FINANCEIRO/ADMIN | Aprovar → APROVADA |
| POST | `/api/commissions/periods/{uuid}/reject/` | FINANCEIRO/ADMIN | Rejeitar → EM_CONFERENCIA |
| POST | `/api/commissions/periods/{uuid}/mark-paid/` | FINANCEIRO/ADMIN | Marcar paga → PAGA + notificações |

### 9.3 Permission classes (app/api/permissions.py)

| Classe | Função |
|--------|--------|
| `IsManagerOrAdmin` | Acesso: MANAGER ou ADMIN. Object: verifica tenant. |
| `IsFinancialOrAdmin` | Acesso: FINANCEIRO ou ADMIN. Object: verifica tenant. |
| `IsSellerOwner` | Acesso: SELLER. Object: verifica `request.user.seller_profile`. |

### 9.4 Máquina de estados (CommissionPeriod)

```
ABERTA → [close] → EM_CONFERENCIA → [send] → ENVIADA_FINANCEIRO
                                                    ↓
                                    [approve] → APROVADA → [mark-paid] → PAGA
                                    [reject]  → EM_CONFERENCIA
```

- Cada transição valida o status atual; pular etapa retorna 400.
- `close` chama `SellerCommission.recalculate()` para cada vendedor.
- `mark-paid` dispara `notify_commission_paid()` para cada vendedor.

### 9.5 Documentação
- Swagger UI: `/api/schema/swagger-ui/`
- Schema OpenAPI: `/api/schema/`

---

## 10. Webhooks

Bug conhecido de correlação em `webhooks/tasks.py` — `data.get('code')` para matching Order.
Fora de escopo. Não corrigir.

---

## 11. Migrations (resumo completo)

| App | Arquivo | Conteúdo |
|-----|---------|----------|
| accounts | `0001_initial` | Tenant, User |
| accounts | `0002_add_user_and_commission_rate` | +`Tenant.default_commission_rate` |
| sellers | `0001_initial` | Seller (sem user) |
| sellers | `0002_add_user_and_commission_rate` | +`user` (nullable), +`commission_rate` |
| sellers | `0003_link_sellers_to_users` | Data migration (RunPython) |
| sellers | `0004_make_user_required` | `user` → non-nullable |
| notifications | `0001_generalize_notification` | Create MessageTemplate + Notification |
| notifications | `0002_create_default_templates` | Data migration (templates padrão) |
| orders | `0001_initial` | Order, PaymentLink |
| payments | `0001_initial` | Payment |
| sales | `0001_initial` | Sale |
| commissions | `0001_initial` | CommissionPeriod, SellerCommission |

---

## 12. Testes

**Suíte completa:** `python manage.py test app.apps.sellers app.apps.notifications app.apps.dashboard`

### 12.1 sellers (10 testes)

| Teste | Cobertura |
|-------|-----------|
| `test_seller_cannot_be_in_sale_of_other_tenant` | Isolamento multi-tenant — `full_clean()` lança `ValidationError` |
| `test_seller_sales_all_no_recursion` | Regressão: `seller.sales.count()` não dispara exceção |
| `test_generate_unique_usernames_no_collision` | Username único com slugify + sufixo |
| `test_link_sellers_to_users_creates_unique_usernames` | Data migration — integração |
| `test_recalculate_sums_sales_correctly` | `SellerCommission.recalculate()` — soma + comissão |
| `test_recalculate_empty_period` | `recalculate()` com 0 vendas |
| `test_reset_password_generates_valid_password` | Comando gera senha ≠ anterior |
| `test_reset_password_allows_login` | Login funciona com nova senha |
| `test_reset_password_output_contains_username` | Output contém dados do vendedor |
| `test_nonexistent_seller_raises_error` | UUID inválido → `CommandError` |

### 12.2 notifications (14 testes)

| Teste | Cobertura |
|-------|-----------|
| `test_notification_without_order_using_seller` | Criação sem Order, com Seller |
| `test_notification_with_commission_period` | Criação com CommissionPeriod |
| `test_tenant_auto_filled_from_seller` | `tenant` auto-preenchido no `save()` |
| `test_default_status_is_pending` | Status padrão = PENDING |
| `test_render_body_seller_credentials` | Template render com variáveis de vendedor |
| `test_render_body_commission_paid` | Template render com variáveis de comissão |
| `test_create_and_send_notification_renders_template` | Template → Notification completo |
| `test_fallback_body_when_no_template` | Fallback sem template cadastrado |
| `test_notify_seller_credentials` | Fluxo completo seller credentials |
| `test_notify_commission_paid` | Fluxo completo commission paid |
| `test_send_whatsapp_notification_success` | Task marca SENT no sucesso |
| `test_send_whatsapp_notification_max_retries_exceeded` | Task marca FAILED após max retries |
| `test_send_whatsapp_notification_retry_on_failure` | Incrementa retry_count + grava error_log |
| `test_skip_non_pending_notification` | Ignora notificação já SENT |

### 12.3 dashboard (6 testes)

| Teste | Cobertura |
|-------|-----------|
| `test_get_seller_create_page` | GET retorna 200 + formulário |
| `test_create_seller_success` | POST cria Seller + User + commission_rate |
| `test_seller_created_even_when_whatsapp_fails` | Falha no WhatsApp não bloqueia criação |
| `test_duplicate_name_gets_unique_username` | Nome duplicado → suffix `-2` |
| `test_missing_name_shows_error` | Campo nome vazio → erro |
| `test_missing_phone_shows_error` | Campo telefone vazio → erro |

### 12.4 api (25 testes)

| Teste | Cobertura |
|-------|-----------|
| `test_jwt_login_returns_tokens` | Login JWT retorna access + refresh |
| `test_jwt_login_invalid_credentials` | Credenciais inválidas → 401 |
| `test_jwt_refresh_returns_new_access` | Refresh gera novo access token |
| `test_unauthenticated_request_returns_401` | Sem token → 401 |
| `test_authenticated_request_with_bearer` | Com Bearer → 200 |
| `test_manager_can_create_seller` | POST /api/sellers/ como MANAGER → 201 |
| `test_seller_cannot_create_another_seller` | SELLER tenta criar vendedor → 403 |
| `test_manager_can_list_sellers` | GET /api/sellers/ como MANAGER |
| `test_seller_cannot_list_sellers` | SELLER tenta listar → 403 |
| `test_seller_can_create_own_sale` | SELLER cria própria venda → 201 |
| `test_sale_amount_is_integer` | amount é int (centavos) no JSON |
| `test_seller_can_view_own_sales` | SELLER vê apenas suas vendas |
| `test_manager_can_view_all_sales` | MANAGER vê todas vendas do tenant |
| `test_manager_a_cannot_see_tenant_b_sellers` | Isolamento multi-tenant sellers |
| `test_manager_a_cannot_see_tenant_b_sales` | Isolamento multi-tenant sales |
| `test_seller_a_cannot_see_tenant_b_sales` | SELLER só vê seu tenant |
| `test_cannot_create_sale_for_other_tenant_seller` | Cross-tenant sale → 400 |
| `test_seller_cannot_view_sales_of_another_seller` | SELLER não vê vendas de outro seller |
| `test_close_aberta_works` | ABERTA → close → EM_CONFERENCIA |
| `test_cannot_close_twice` | Fechar 2x → 400 |
| `test_cannot_approve_before_send` | Aprovar antes de enviar → 400 |
| `test_full_flow_close_send_approve_mark_paid` | Fluxo completo ABERTA→PAGA |
| `test_reject_sends_back_to_conferencia` | Rejeitar volta p/ EM_CONFERENCIA |
| `test_manager_cannot_approve` | MANAGER tenta aprovar → 403 |
| `test_cannot_mark_paid_before_approved` | mark-paid antes de APPROVED → 400 |

---

## 13. Histórico de Implementação

### Lote 0 — Reconhecimento (2026-06-20)
- Mapeamento de apps, dependências, models, banco
- Identificação de lixo (`Não`, `pyproject.toml`, `uv.lock`, `req.txt`)
- Confirmação: `requirements/base.txt` + `requirements/production.txt` é a fonte real
- Banco local vazio (0 registros), 16 sellers prontos no `seed.py`

### Lote 1 — Models, Seller↔User, Migração (2026-06-20)
- `Seller.user` (OneToOneField, CASCADE, `related_name='seller_profile'`)
- `Seller.commission_rate` (DecimalField, default=0.01)
- `Tenant.default_commission_rate` (DecimalField, default=0.01)
- Data migration `0003_link_sellers_to_users` — cria User para Sellers órfãos
- Comando `reset_seller_password`
- Cross-tenant validation em `Sale.clean()`
- **10 testes passando**

### Lote 1.5 — Cadastro de Vendedor + Notificações WhatsApp (2026-06-20)
- Generalização de `Notification` (nullable `order`, +`seller`, +`commission_period`, +`event_type`, +`tenant`)
- `MessageTemplate.EventType` com `SELLER_CREDENTIALS` e `COMMISSION_PAID`
- Task Celery `send_whatsapp_notification` com retry/backoff
- Funções `notify_seller_credentials()` e `notify_commission_paid()`
- Data migration de templates padrão por tenant
- Tela `/dashboard/sellers/create/` (view + template + URL)
- **30 testes passando** (10 sellers + 14 notifications + 6 dashboard)

### Lote 2 — API REST, JWT, Permissões multi-tenant (2026-06-20)
- App `api` com `DefaultRouter` + `ViewSet`s
- JWT via `djangorestframework-simplejwt` (access 30min, refresh 7d)
- Rate limiting no login (5/min, IP + username)
- 3 permission classes: `IsManagerOrAdmin`, `IsFinancialOrAdmin`, `IsSellerOwner`
- Todas as permissions verificam tenant em every request
- Máquina de estados do `CommissionPeriod` com validação de transições
- `mark_paid` chama `notify_commission_paid()` para cada vendedor
- `close` chama `SellerCommission.recalculate()` para cada vendedor
- Swagger UI em `/api/schema/swagger-ui/`
- Dependências: `djangorestframework-simplejwt`, `drf-spectacular`, `django-ratelimit`
- **55 testes passando** (10 sellers + 14 notifications + 6 dashboard + 25 api)

### Lote 3 — Telas mobile (vendedor) + PWA (2026-06-20)
- Decisão Alpine.js (documentada no README)
- `SessionAuthentication` adicionado ao DRF
- 5 telas mobile: login, home, lançar venda, minhas vendas, meu desempenho
- Máscara monetária: entrada visual em R$ (Alpine.js), submit em centavos inteiros
- Layout mobile com Tailwind CDN + bottom nav fixo (3 ícones)
- Exclusão de venda: botão visível só para vendas do dia; validação real no backend
- Forgot password: tela informativa "procure seu gestor" (sem e-mail)
- PWA: manifest.json (8 ícones), sw.js (cache + offline fallback)
- Prompt de instalação via `beforeinstallprompt` (aparece após 2+ logins)
- **55 testes passando** (sem regressão) + **9 passos de fluxo mobile verificados**

### Lote 4 — Telas desktop + Infraestrutura final (2026-06-20)
- Tailwind: `package.json`, `tailwind.config.js`, `Dockerfile` multi-stage (Node build → Python runtime)
- 5 telas desktop: ranking (Chart.js), vendedores (CRUD visual + reset senha), fechamento, fila aprovação, histórico pagamentos
- Audit logging em approve/reject + comando reset_seller_password
- API: +`/api/manager/ranking/`, +`/api/manager/commissions/<status>/`, +CSV export, +`reset_password` action
- Sidebar desktop com navegação role-based
- Exportação CSV via stdlib
- Fluxo MVP completo verificado: cadastro → lançamento → fechamento → aprovação → pagamento
- **55 testes passando** + fluxo end-to-end com audit logs confirmados

### Lote 4.5 — Design system e refinamento visual (2026-06-20)
- `tailwind.config.js` com tokens `brand-*`, `success-*`, `warning-*`, `danger-*` (50/500/700)
- 5 component partials: `_button.html`, `_metric_card.html`, `_status_badge.html`, `_bottom_nav.html`, `_sidebar.html`
- 10 telas refatoradas com partials; zero cores hardcoded (`blue-*`, `green-*`, `red-*`)
- Tipografia Inter com hierarquia 28/18/14/13px
- Botão "Lançar venda" em `success-500`; botão "Devolver" como outline `danger-500`
- Gráfico ranking: líder em `brand-700` (#2540ad), demais em tom claro
- Zero mudanças em views/endpoints — só templates/CSS

### Lote 5 — Validação em staging (2026-06-20)
- Migrations do zero: todas 12 aplicam em ordem correta
- Seed atualizado para criar `User` + `Seller` (16 vendedores Bibelô)
- Validação local: centavos sem arredondamento (R$ 47,90), recalculate == manual (57990)
- Máquina de estados: ABERTA → PAGA sem erro
- `VALIDATION_CHECKLIST_LOTE5.md` com 52 itens
- `READINESS_REPORT.md`: ✅ PRONTO PARA STAGING
- Pendente: provisionar Coolify staging, testar WhatsApp real, validar PWA em celular real

---

## 16. Deploy e Automação

### entrypoint.sh (inicialização automática)
1. Aguarda banco de dados responder (30 tentativas, 2s cada)
2. `makemigrations --noinput` + `migrate --noinput`
3. Seed opcional via `SEED_ON_START=true` (16 vendedores criados automaticamente)
4. Templates padrão de notificação garantidos (idempotente)
5. `collectstatic --noinput`
6. Gunicorn com workers configuráveis (`GUNICORN_WORKERS`, `GUNICORN_TIMEOUT`)

### docker-compose.yml
4 serviços: `web` (Gunicorn), `querolink-redis` (broker), `celery_worker`, `celery_beat`
- Healthchecks em todos os serviços
- `start_period: 30s` no web para aguardar migrations
- Volumes: `static_volume`, `media_volume`, `redis_data`
- Rede `coolify` externa

### Dockerfile multi-stage
- Estágio 1: `node:20-alpine` → `npm install` → `tailwindcss` build → `tailwind.css`
- Estágio 2: `python:3.12.3-slim` → `pip install` → copia `tailwind.css` → sem Node na imagem final

### Makefile
17 comandos: `make help`, `dev`, `test`, `seed`, `build`, `up`, `down`, `logs`, `reset-db`, `deploy-check`, `clean`, etc.

### scripts/deploy.sh
Deploy com um comando: `scripts/deploy.sh [staging|production]`
- Valida `.env`
- Produção: tenta backup `pg_dump`
- `docker compose build --no-cache` + `up -d`
- Aguarda healthcheck
- Exibe logs

### .env.example
Todas as 18 variáveis documentadas: Django, banco, Redis, APIs externas, JWT, Gunicorn, URLs

---

## 14. Consolidação Final do MVP — Decisões por Lote

### Lote 1
- `Seller.user`: `on_delete=CASCADE` desde a primeira migration (não `SET_NULL`)
- `commission_rate` no Seller copia `Tenant.default_commission_rate` no cadastro; não é fallback dinâmico
- Username gerado via `slugify(name)` + sufixo numérico se colisão

### Lote 1.5
- `Notification` generalizada com 3 FKs opcionais (não `GenericForeignKey`) — mais simples para 2-3 tipos
- Templates padrão `SELLER_CREDENTIALS` e `COMMISSION_PAID` via data migration
- Senha temporária impressa na tela UMA vez; WhatsApp assíncrono não bloqueante

### Lote 2
- `TokenAuthentication` mantido em paralelo com JWT (legacy)
- SELLER auto-força próprio `seller_profile` no `SaleCreateSerializer` (ignora payload)
- Rate limit usa `LocMemCache`; testes limpam cache no `setUp`
- `django-ratelimit` com `block=True` retorna 403

### Lote 3
- Alpine.js sobre HTMX: API JSON, reatividade sem build step
- Sessão Django para mobile (não JWT em localStorage)
- Tailwind CDN para dev; build compilado no Docker para prod
- Forgot password: tela informativa, sem fluxo automático

### Lote 4
- Chart.js para gráfico de ranking (não lib mais pesada)
- CSV export via `csv` stdlib (sem dependência nova)
- Audit via model `AuditLog` existente (não mecanismo paralelo)
- `mark_paid`: URL path com underscore (`mark_paid/`), nome reverso com hífen (`api-commission-period-mark-paid`)

---

## 15. Arquivos de Resíduo (identificados, NÃO removidos)

| Arquivo | Motivo |
|---------|--------|
| `./Não` | 0 bytes, erro de encoding no nome |
| `req.txt` | Encoding quebrado, faltam celery/redis |
| `requirements.txt` (raiz) | Duplicata do `base.txt` |
| `pyproject.toml` | `dependencies=[]` vazio, resíduo `uv init` |
| `uv.lock` | Nunca usado |
| `nova_base.html` | Rascunho com sellers hardcoded, não referenciado |
| `messages.pot` | Resíduo de i18n |
