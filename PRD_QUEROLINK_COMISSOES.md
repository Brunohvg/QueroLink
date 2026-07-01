# PRD — VendaPay / QueroLink Sistema de Comissões

> **Status geral:** ✅ PRODUÇÃO — Lotes 1 a 7 implementados. Sistema estabilizado.
> **Versão:** 2.1.0 | **Última atualização:** 2026-07-01
> **Recomendação:** ✅ PRONTO PARA PRODUÇÃO

---

## 1. Visão Geral

VendaPay é um sistema multi-tenant de gestão de comissões para vendedores de lojas.
O tenant principal é a Loja Bibelô, com 16 vendedores reais.

## 2. Arquitetura

- Django 5.1 + PostgreSQL
- Celery + Redis para tarefas assíncronas (envio de WhatsApp via Evolution API)
- Multi-tenant via model Tenant, com isolamento lógico nos models
- Custom User model com roles: ADMIN, MANAGER, FINANCEIRO, SELLER
- Integração WhatsApp: Evolution API (`api.lojabibelo.com.br`), client em `app/services/messaging/whatsapp.py`
- CSP middleware ativo com `unsafe-inline` + `unsafe-eval` para Alpine.js
- Dependência real: `requirements/base.txt` + `requirements/production.txt` (Dockerfile)

## 3. Apps

| App | Função | Status |
|-----|--------|--------|
| accounts | Tenant, User customizado, middleware CSP, backup task | ✅ Lote 1 |
| sellers | Seller (vendedor) | ✅ Lote 1 + 1.5 |
| orders | Order, PaymentLink, services (link creation) | ✅ |
| payments | Payment (paid_at, card_brand, card_last4, gateway IDs) | ✅ |
| sales | Sales (vendas lançadas, origens LINK + MANUAL) | ✅ |
| commissions | CommissionPeriod, SellerCommission, services | ✅ Lote 7 |
| webhooks | WebhookEvent (Pagar.me), 9 eventos tratados, dedup, cleanup | ✅ Lote 6 + 7 |
| notifications | Templates + envio WhatsApp | ✅ Lote 1.5 |
| analytics | Analytics de cliques (LinkClick) | ❌ Model existe, view não implementada |
| audit | Auditoria (AuditLog) | ✅ |
| dashboard | Dashboard gestor/financeiro + mobile vendedor + link detalhe | ✅ Lotes 1-6 |
| api | REST API (25+ endpoints, rate limiting, JWT logout) | ✅ Lote 2-6 |

---

## 4. Models

### 4.1 Seller (app `sellers`)

```python
class Seller(models.Model):
    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name='sellers')
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='seller_profile')
    name = models.CharField(max_length=100)
    phone = models.CharField(max_length=20)
    commission_rate = models.DecimalField(max_digits=5, decimal_places=4, default=0.01)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
```

### 4.2 Tenant (app `accounts`)

```python
class Tenant(models.Model):
    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company_name = models.CharField(max_length=255)
    cnpj = models.CharField(max_length=14, blank=True, null=True, unique=True, db_index=True)
    cnpj_hash = models.CharField(max_length=64, blank=True, null=True, unique=True, db_index=True)
    slug = models.SlugField(max_length=100, unique=True, blank=True)
    pagarme_api_key = EncryptedCharField(max_length=600, blank=True, null=True)
    pagarme_webhook_username = EncryptedCharField(max_length=600, blank=True, null=True)
    pagarme_webhook_password = EncryptedCharField(max_length=600, blank=True, null=True)
    whatsapp_instance_id = models.CharField(max_length=100, blank=True, null=True)
    whatsapp_token = EncryptedCharField(max_length=600, blank=True, null=True)
    default_commission_rate = models.DecimalField(max_digits=5, decimal_places=4, default=0.01)
    link_expires_in = models.PositiveIntegerField(default=1200)
    pix_enabled = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    trial_expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
```

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

### 4.4 Order (app `orders`)

```python
class Order(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        COMPLETED = 'COMPLETED', 'Completed'
        EXPIRED = 'EXPIRED', 'Expired'
        CANCELED = 'CANCELED', 'Canceled'
        SUSPENDED = 'SUSPENDED', 'Suspended'

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name='orders')
    seller = models.ForeignKey(Seller, on_delete=models.SET_NULL, null=True, related_name='orders')
    customer_name = EncryptedCharField(max_length=600)
    customer_phone = EncryptedCharField(max_length=600, blank=True, null=True)
    total_amount = models.PositiveIntegerField(help_text="Value in cents")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['tenant', 'status']),
            models.Index(fields=['tenant', 'seller', 'created_at']),
        ]
```

### 4.5 PaymentLink (app `orders`)

```python
class PaymentLink(models.Model):
    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name='payment_link')
    short_code = models.CharField(max_length=20, unique=True, blank=True, null=True)
    gateway_url = models.URLField(max_length=500, blank=True, null=True)
    gateway_link_id = models.CharField(max_length=100, blank=True, null=True, db_index=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    opened_at = models.DateTimeField(null=True, blank=True)
    clicks_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
```

### 4.6 Payment (app `payments`)

```python
class Payment(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        PAID = 'PAID', 'Paid'
        FAILED = 'FAILED', 'Failed'
        REFUNDED = 'REFUNDED', 'Refunded'
        CHARGEBACK = 'CHARGEBACK', 'Chargeback'

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='payments')
    gateway_name = models.CharField(max_length=50, default='pagarme')
    gateway_order_id = models.CharField(max_length=100, blank=True, null=True)
    gateway_transaction_id = models.CharField(max_length=100, unique=True, blank=True, null=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    payment_method = models.CharField(max_length=20, blank=True, null=True)
    installments = models.IntegerField(default=1)
    paid_at = models.DateTimeField(null=True, blank=True)
    card_brand = models.CharField(max_length=20, blank=True, null=True)
    card_last4 = models.CharField(max_length=4, blank=True, null=True)
    raw_callback_payload = models.JSONField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
```

### 4.7 Sale (app `sales`)

```python
class Sale(models.Model):
    class Origin(models.TextChoices):
        LINK = 'LINK', 'Link'
        MANUAL = 'MANUAL', 'Manual'

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name='sales')
    seller = models.ForeignKey(Seller, on_delete=models.CASCADE, related_name='sales')
    order = models.ForeignKey(Order, on_delete=models.SET_NULL, null=True, blank=True, related_name='sales')
    origin = models.CharField(max_length=10, choices=Origin.choices, default=Origin.MANUAL)
    amount = models.PositiveIntegerField(help_text="Value in cents")
    sale_date = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
```

### 4.8 CommissionPeriod (app `commissions`)

```python
class CommissionPeriod(models.Model):
    class Status(models.TextChoices):
        ABERTA = 'ABERTA', 'Aberta'
        FECHADA = 'FECHADA', 'Fechada'
        PARCIALMENTE_FECHADA = 'PARCIALMENTE_FECHADA', 'Parcialmente Fechada'
        PAGA = 'PAGA', 'Paga'
        PARCIALMENTE_PAGA = 'PARCIALMENTE_PAGA', 'Parcialmente Paga'
        CANCELADA = 'CANCELADA', 'Cancelada'

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name='commission_periods')
    month = models.PositiveSmallIntegerField()
    year = models.PositiveSmallIntegerField()
    expected_working_days = models.PositiveSmallIntegerField(default=22)
    status = models.CharField(max_length=25, choices=Status.choices, default=Status.ABERTA)
    notes = models.TextField(blank=True, null=True)
    cancelled_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='cancelled_periods')
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_reason = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('tenant', 'month', 'year')]
```

### 4.9 SellerCommission (app `commissions`)

```python
class SellerCommission(models.Model):
    class Status(models.TextChoices):
        ABERTA = 'ABERTA', 'Aberta'
        FECHADA = 'FECHADA', 'Fechada'
        PAGA = 'PAGA', 'Paga'
        AJUSTADA = 'AJUSTADA', 'Ajustada'
        REABERTA = 'REABERTA', 'Reaberta'
        CANCELADA = 'CANCELADA', 'Cancelada'

    class OperationalStatus(models.TextChoices):
        PRONTO = 'PRONTO', 'Pronto'
        PENDENTE = 'PENDENTE', 'Pendente'
        SEM_LANCAMENTO = 'SEM_LANCAMENTO', 'Sem Lancamento'

    period = models.ForeignKey(CommissionPeriod, on_delete=models.CASCADE, related_name='seller_commissions')
    seller = models.ForeignKey(Seller, on_delete=models.CASCADE, related_name='commissions')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ABERTA)
    # ... (campos de valores, datas, métodos freeze/recalculate/reopen/mark_paid)
    # Campo gateway_link_id com db_index=True (Lote 7)
```

### 4.10 WebhookEvent (app `webhooks`)

```python
class WebhookEvent(models.Model):
    gateway = models.CharField(max_length=50)
    gateway_event_id = models.CharField(max_length=100, blank=True, null=True, unique=True)
    payload = models.JSONField()
    processed = models.BooleanField(default=False)
    processing_error = models.TextField(blank=True, null=True)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, null=True, blank=True, related_name='webhook_events')
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['gateway', 'received_at']),
            models.Index(fields=['processed']),
            models.Index(fields=['gateway_event_id']),
        ]
```

---

## 5. Comandos de Management

### `reset_seller_password`

```bash
python manage.py reset_seller_password <seller_uuid>
```

Gera nova senha temporária para o vendedor, aplica via `set_password()`, imprime no terminal.

---

## 6. Tarefas Assíncronas (Celery)

| Task | Arquivo | Time Limits | Descrição |
|------|---------|:-----------:|-----------|
| `process_pagarme_webhook` | `webhooks/tasks.py` | 120s/180s | Processa eventos do Pagar.me com dedup e retry 3x |
| `cleanup_old_webhook_events` | `webhooks/tasks.py` | 300s/360s | Remove eventos >90 dias em lotes de 1000 |
| `send_whatsapp_notification` | `notifications/tasks.py` | 60s/90s | Envia WhatsApp com retry exponencial 3x |
| `daily_backup` | `accounts/tasks.py` | 360s/420s | Backup pg_dump → Google Drive via rclone |

---

## 7. Fluxos Implementados

### 7.1 Cadastro de vendedor pelo gestor
- URL: `POST /dashboard/sellers/create/`
- Autenticação: `@login_required`
- Gera username + senha temporária, cria User + Seller
- Dispara `notify_seller_credentials()` (WhatsApp) — falha não bloqueia

### 7.2 Criação de link de pagamento
- `orders/services.py:create_payment_link` — orquestra criação no Pagar.me
- Em caso de falha no banco após criar link no gateway, cancela o link órfão

### 7.3 Processamento de webhook
- View: `pagarme_webhook` — recebe POST, dedup via `gateway_event_id`, autenticação Basic Auth opcional
- Task: `process_pagarme_webhook` — idempotente, com `select_for_update(nowait=True)`, trata 9 tipos de evento

### 7.4 Fechamento de comissões
- `close_seller_commissions` — usa `select_for_update` com `order_by('id')` para evitar deadlocks
- `reopen_seller_commissions` — mesmo padrão
- `pay_seller_commissions` — mesmo padrão, notifica vendedores
- `create_commission_adjustment` — atomico com `select_for_update`

---

## 8. Telas

### 8.1 Mobile PWA (vendedor)

| Tela | URL | Status |
|------|-----|--------|
| Login | `/dashboard/mobile/login/` | ✅ |
| Home (resumo do mês) | `/dashboard/mobile/` | ✅ |
| Lançar venda | `/dashboard/mobile/lancar/` | ✅ |
| Minhas vendas | `/dashboard/mobile/vendas/` | ✅ |
| Links de pagamento | `/dashboard/mobile/links/` | ✅ |
| Fechamento | `/dashboard/mobile/fechamento/` | ✅ |
| Esqueci senha | `/dashboard/mobile/forgot-password/` | ✅ |

### 8.2 Desktop (gestor)

| Tela | URL | Status |
|------|-----|--------|
| Login | `/dashboard/login/` | ✅ |
| Home (dashboard) | `/dashboard/gestor/` | ✅ |
| Ranking | `/dashboard/gestor/ranking/` | ✅ |
| Vendedores | `/dashboard/gestor/vendedores/` | ✅ |
| Links | `/dashboard/gestor/links/` | ✅ |
| Detalhe do link | `/dashboard/gestor/links/{uuid}/` | ✅ |
| Fechamento | `/dashboard/gestor/fechamento/` | ✅ |
| Configurações | `/dashboard/gestor/configuracoes/` | ✅ |

### 8.3 Desktop (financeiro)

| Tela | URL | Status |
|------|-----|--------|
| Fila de aprovação | `/dashboard/financeiro/fila/` | ✅ |
| Histórico de pagamentos | `/dashboard/financeiro/historico/` | ✅ |

### 8.4 Público

| Tela | URL | Status |
|------|-----|--------|
| Link de pagamento | `/<tenant_slug>/` | ✅ |
| Pagamento concluído | `/pago/<order_uuid>/` | ✅ |

---

## 9. API REST

### 9.1 Autenticação
- JWT via `djangorestframework-simplejwt`
- `POST /api/auth/login/` — login (rate limit 5/min por IP + username)
- `POST /api/auth/refresh/` — refresh token
- `POST /api/auth/logout/` — blacklist do refresh token
- Variáveis de ambiente: `JWT_ACCESS_TOKEN_LIFETIME_MINUTES` (default 30), `JWT_REFRESH_TOKEN_LIFETIME_DAYS` (default 7)

### 9.2 Endpoints

| Método | URL | Permissão | Descrição |
|--------|-----|-----------|-----------|
| GET/POST | `/api/sellers/` | MANAGER/ADMIN | Listar/criar vendedores |
| GET/PUT/DELETE | `/api/sellers/{uuid}/` | MANAGER/ADMIN | Detalhe/editar/excluir vendedor |
| POST | `/api/sellers/{uuid}/reset_password/` | MANAGER/ADMIN | Resetar senha do vendedor |
| POST | `/api/sellers/import_sellers/` | MANAGER/ADMIN | Importar CSV/XLSX |
| GET/POST | `/api/sales/` | SELLER/MANAGER/ADMIN | Listar/criar vendas |
| GET/PUT/DELETE | `/api/sales/{uuid}/` | SELLER/MANAGER | Detalhe/editar/excluir venda |
| GET | `/api/seller/sales/` | SELLER | Vendas do próprio vendedor |
| GET | `/api/manager/sales/` | MANAGER/ADMIN | Todas vendas do tenant |
| GET | `/api/manager/seller/{uuid}/` | MANAGER/ADMIN | Detalhe do vendedor (vendas + comissões + evolução) |
| GET | `/api/manager/seller/{uuid}/csv/` | MANAGER/ADMIN | Exportar CSV |
| GET | `/api/manager/seller/{uuid}/xlsx/` | MANAGER/ADMIN | Exportar XLSX |
| GET | `/api/manager/seller/{uuid}/pdf/` | MANAGER/ADMIN | Exportar PDF |
| GET/POST | `/api/seller/links/` | SELLER | Criar/listar links pagamento (rate limit 10/min) |
| GET/POST | `/api/commissions/periods/` | MANAGER/ADMIN | Listar/criar competências |
| GET/PATCH/DELETE | `/api/commissions/periods/{uuid}/` | MANAGER/ADMIN | Detalhe/editar/excluir competência |
| POST | `/api/commissions/periods/{uuid}/sync/` | MANAGER/ADMIN | Sincronizar vendedores |
| POST | `/api/commissions/periods/{uuid}/close_sellers/` | MANAGER/ADMIN | Fechar comissões selecionadas |
| POST | `/api/commissions/periods/{uuid}/reopen_sellers/` | MANAGER/ADMIN | Reabrir comissões |
| POST | `/api/commissions/periods/{uuid}/pay_sellers/` | FINANCEIRO/ADMIN | Marcar comissões como pagas |
| POST | `/api/commissions/periods/{uuid}/cancel/` | MANAGER/ADMIN | Cancelar competência |
| GET | `/api/manager/commissions/{status}/` | MANAGER/ADMIN | Filtrar por status |
| GET | `/api/manager/ranking/` | MANAGER/ADMIN | Ranking do mês |
| GET | `/api/manager/ranking/annual/` | MANAGER/ADMIN | Ranking anual top 5 |
| GET | `/api/manager/dashboard/summary/` | MANAGER/ADMIN | Dashboard resumo |
| GET | `/api/manager/webhook-status/` | MANAGER/ADMIN | Status do webhook |
| GET | `/api/financial/payment-queue/` | FINANCEIRO/ADMIN | Fila de pagamentos |
| GET | `/api/financial/commissions/{uuid}/csv/` | FINANCEIRO/ADMIN | Exportar CSV financeiro |
| POST | `/api/seller/change-password/` | SELLER | Alterar própria senha |

### 9.3 Permission classes

| Classe | Função |
|--------|--------|
| `IsManagerOrAdmin` | Acesso: MANAGER ou ADMIN. Object: verifica tenant. |
| `IsFinancialOrAdmin` | Acesso: FINANCEIRO ou ADMIN. Object: verifica tenant. |
| `IsSellerOwner` | Acesso: SELLER. Object: verifica `request.user.seller_profile`. |

### 9.4 Documentação
- Swagger UI: `/api/schema/swagger-ui/`
- Schema OpenAPI: `/api/schema/`

---

## 10. Webhooks

### 10.1 Endpoint
`POST /api/webhooks/pagarme/<tenant_slug>/`

### 10.2 Autenticação
- Basic Auth opcional configurado por tenant no painel de configurações
- Se `pagarme_webhook_username` e `pagarme_webhook_password` estão configurados, a autenticação é exigida

### 10.3 Deduplicação
- Eventos com `id` no formato `evt_xxx` são deduplicados na view (retorna 200 "duplicate")
- Na task, verifica se outro evento com mesmo `gateway_event_id` já foi processado
- Campo `gateway_event_id` com `unique=True` no banco

### 10.4 Eventos tratados

| Evento | Ação |
|--------|------|
| `charge.paid` | Payment → PAID, Order → COMPLETED, cria Sale, notifica WhatsApp |
| `charge.payment_failed` | Payment → FAILED, notifica com motivo |
| `charge.refunded` | Payment → REFUNDED, remove Sale, notifica |
| `charge.chargedback` | Payment → CHARGEBACK, remove Sale, notifica |
| `charge.antifraud_reproved` | Payment → FAILED, notifica |
| `order.paid` | Igual charge.paid via charges[0] |
| `payment-link.finished` | Igual charge.paid via gateway_link_id |
| `payment-link.expired` | Order → EXPIRED, notifica |
| `payment-link.cancelled` | Order → CANCELED, notifica |

### 10.5 Task Celery
- `process_pagarme_webhook`: retry 3x com 30s, `select_for_update(nowait=True)`
- `cleanup_old_webhook_events`: diário, remove eventos >90 dias em lotes de 1000

---

## 11. Migrations (resumo completo)

### accounts
| Migration | Conteúdo |
|-----------|----------|
| `0001_initial` | Tenant, User |
| `0002` | +`Tenant.default_commission_rate` |
| `0003` | +`Tenant.cnpj` |
| `0004` | +`Tenant.cnpj_hash` |
| `0005` | Altera Tenant.cnpj_hash para unique |
| `0006` | +`Tenant.slug` (raw SQL) |
| `0007` | +`Tenant.link_expires_in`, +`Tenant.pix_enabled` |
| `0008` | +`Tenant.trial_expires_at` |
| `0009_0010_0011` | Ajustes de campos |
| `0012` | +`pagarme_webhook_username`, +`pagarme_webhook_password` |

### sellers
| Migration | Conteúdo |
|-----------|----------|
| `0001_initial` | Seller |
| `0002` | +`user` (nullable), +`commission_rate` |
| `0003` | Data migration: link sellers to users |
| `0004` | user → non-nullable |

### orders
| Migration | Conteúdo |
|-----------|----------|
| `0001_initial` | Order, PaymentLink |
| `0002` | Altera campos criptografados |
| `0003` | +`db_index` em `gateway_link_id` |

### webhooks
| Migration | Conteúdo |
|-----------|----------|
| `0001_initial` | WebhookEvent |
| `0002` | +`tenant` FK |
| `0003` | +`gateway_event_id` + index |

### notifications
| Migration | Conteúdo |
|-----------|----------|
| `0001_generalize_notification` | MessageTemplate, Notification |
| `0002_create_default_templates` | Data migration |

### commissions
| Migration | Conteúdo |
|-----------|----------|
| `0001_initial` | CommissionPeriod, SellerCommission, CommissionAdjustment |
| `0002` | Ajustes de campos de comissão |

---

## 12. Testes

### 12.1 sellers (10 testes)
- Isolamento multi-tenant, username único, data migration, recalculate, reset password

### 12.2 notifications (14 testes)
- Criação, templates, fallback, retry WhatsApp, max retries

### 12.3 dashboard (6 testes)
- CRUD vendedor, username único, falha WhatsApp não bloqueia

### 12.4 api (25 testes)
- JWT login/refresh/logout, permissões multi-tenant, CRUD sellers/sales, máquina de estados

**Total: 55 testes**

---

## 13. Histórico de Implementação

### Lote 0 — Reconhecimento (2026-06-20)
- Mapeamento de apps, dependências, models, banco
- Identificação de resíduos

### Lote 1 — Models, Seller↔User, Migração (2026-06-20)
- Seller.user, commission_rate, Tenant.default_commission_rate
- Data migration link_sellers_to_users
- Comando reset_seller_password
- Cross-tenant validation em Sale

### Lote 1.5 — Cadastro + Notificações WhatsApp (2026-06-20)
- Generalização Notification, MessageTemplate
- Task Celery send_whatsapp_notification com retry
- Tela de cadastro de vendedor

### Lote 2 — API REST, JWT, Permissões (2026-06-20)
- ViewSets, JWT simplesjwt, rate limiting
- 3 permission classes
- Máquina de estados CommissionPeriod
- Swagger UI

### Lote 3 — Telas mobile + PWA (2026-06-20)
- Alpine.js, 5 telas mobile, PWA

### Lote 4 — Telas desktop + Infraestrutura (2026-06-20)
- Tailwind compilado, 5 telas desktop, Chart.js
- Sidebar role-based, export CSV

### Lote 4.5 — Design system (2026-06-20)
- Tokens Tailwind, component partials, refatoração visual

### Lote 5 — Validação staging (2026-06-20)
- 52 itens validados, 55/55 testes

### Lote 6 — Estabilização e UI (2026-06-29)
**16 correções:**
- C1: UnboundLocalError em payment-link.expired
- C2: Sale deletada em refunded/chargedback
- C3: CSRF token injetado automaticamente no mobile
- C4: WARNING para webhook sem assinatura
- C5: POST /api/auth/logout/ com blacklist
- H3: TrialEnforcementMiddleware (aviso sem bloqueio)
- H4: recalculate() unificado com get_commission_rate()
- H7: _normalize_api_key com rstrip condicional
- M1: Páginas públicas migradas para Tailwind
- M2: Rate limit 10/min em links
- M5: Celery beat cleanup_old_webhook_events
- M6: Anos dinâmicos no filtro
- M7: Validação R$ 100.000 em venda manual
- M8: Template tag currency_filters.brl
- M10: short_code automático em create_payment_link()

**Novas features:** Payment metadata (paid_at, card_brand, card_last4), webhook payment-link.finished, backup Google Drive

### Lote 7 — Correções de concorrência e operação (2026-07-01)
**5 correções:**

| # | Arquivo | Correção |
|---|---------|----------|
| C1 | `commissions/services.py` | Deadlock: `select_for_update` sem `ORDER BY` — adicionado `.order_by('id')` em close, reopen, pay |
| C2 | `commissions/services.py` | Race condition: `create_commission_adjustment` lê sem lock — adicionado `transaction.atomic()` + `select_for_update` |
| C3 | `orders/models.py` | Performance: `PaymentLink.gateway_link_id` sem `db_index` — adicionado + migration 0003 |
| C4 | `webhooks/tasks.py`, `notifications/tasks.py`, `accounts/tasks.py` | Operação: tasks sem time limits — adicionados `soft_time_limit`/`time_limit` em todas as 4 tasks |
| C5 | `webhooks/tasks.py` | Operação: DELETE massivo sem batching — alterado para lotes de 1000 |

**Outras correções:**
| # | Arquivo | Correção |
|---|---------|----------|
| C6 | `csp_middleware.py` | CSP bloqueava scripts inline (Alpine.js quebrava) — adicionado `unsafe-inline` + `unsafe-eval` |
| C7 | `webhooks/models.py`, `webhooks/views.py`, `webhooks/tasks.py` | Dedup de webhooks via `gateway_event_id` |
| C8 | `orders/services.py` | Limpeza de links órfãos no Pagar.me em caso de falha |

---

## 14. Segurança

| Item | Status |
|------|--------|
| DEBUG=False em produção | ✅ |
| HTTPS forçado (SECURE_SSL_REDIRECT) | ✅ |
| HSTS 1 ano + subdomains + preload | ✅ |
| Session + CSRF cookies secure | ✅ |
| Content-Type nosniff | ✅ |
| XSS filter | ✅ |
| Referrer policy same-origin | ✅ |
| CSP middleware ativo (script-src com self + CDNs + unsafe-inline) | ✅ |
| .env no .gitignore | ✅ |
| Dados sensíveis criptografados (EncryptedCharField) | ✅ |
| Rate limiting em login, criação de links, forgot password | ✅ |
| Safe redirect pattern no login | ✅ |
| Proteção multi-tenant em todas as queries | ✅ |
| Cross-tenant validation em Sale.clean() | ✅ |
| 3 permission classes no DRF | ✅ |

---

## 15. Deploy e Automação

### entrypoint.sh
1. Aguarda banco (30 tentativas, 2s cada)
2. migrate + collectstatic
3. Seed opcional via SEED_ON_START
4. Gunicorn

### Docker
- Dockerfile multi-stage (Node → Python + rclone)
- 4 serviços: web, redis, celery_worker, celery_beat
- Healthchecks em todos

### scripts/deploy.sh
- Valida .env, tenta backup pré-deploy, build + up, aguarda healthcheck

---

## 16. Decisões Consolidadas

- Alpine.js sobre HTMX: API JSON, reatividade sem build step
- Sessão Django para mobile (não JWT em localStorage)
- Tailwind compilado no Docker para prod, CDN para dev
- Chart.js para gráficos (não lib mais pesada)
- `AuditLog` existente para auditoria
- `select_for_update` com `order_by('id')` para evitar deadlocks
- Webhook dedup via `gateway_event_id` + `unique=True`
- Tasks com `soft_time_limit` + `time_limit`
- DELETE em lotes de 1000 para limpeza de eventos
