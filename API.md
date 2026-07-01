# API REST — Comissã

> **Base URL:** `https://querolink.lojabibelo.com.br/api/`
> **Schema OpenAPI:** `/api/schema/`
> **Swagger UI:** `/api/schema/swagger-ui/`
> **Versão:** 2.1.0

---

## Autenticação

### JWT Login

```http
POST /api/auth/login/
Content-Type: application/json

{
  "username": "admin@bibelo.com.br",
  "password": "admin123"
}
```

**Rate limit:** 5/min por IP + username (retorna 403 se excedido)

**Resposta 200:**
```json
{
  "refresh": "eyJ...",
  "access": "eyJ..."
}
```

### JWT Refresh

```http
POST /api/auth/refresh/
Content-Type: application/json

{
  "refresh": "eyJ..."
}
```

### JWT Logout

```http
POST /api/auth/logout/
Authorization: Bearer <access_token>
Content-Type: application/json

{
  "refresh": "eyJ..."
}
```

Blacklist do refresh token. Requer autenticação.

### Headers de autenticação

```
Authorization: Bearer <access_token>
# ou
Authorization: Token <token_legacy>
# ou (session cookie para web)
Cookie: sessionid=<session_id>; csrftoken=<csrf_token>
```

---

## Sellers

### Listar / Criar vendedores

```http
GET /api/sellers/
Authorization: Bearer <token>
```

```http
POST /api/sellers/
Authorization: Bearer <token>
Content-Type: application/json

{
  "name": "João Silva",
  "phone": "(31) 99999-8888"
}
```

**Permissão:** MANAGER, ADMIN

**Resposta POST 201:**
```json
{
  "uuid": "abc...",
  "name": "João Silva",
  "phone": "(31) 99999-8888",
  "commission_rate": "0.0100",
  "is_active": true,
  "username": "joao-silva",
  "temp_password": "aB3#kL9$xR2$",
  "whatsapp_sent": true
}
```

### Detalhe / Atualizar / Excluir vendedor

```http
GET /api/sellers/{uuid}/
PATCH /api/sellers/{uuid}/
DELETE /api/sellers/{uuid}/
Authorization: Bearer <token>
```

**PATCH parcial:**
```json
{
  "name": "João Silva Santos",
  "phone": "(31) 99999-7777",
  "commission_rate": "0.0150",
  "is_active": false
}
```

### Resetar senha

```http
POST /api/sellers/{uuid}/reset_password/
Authorization: Bearer <token>
```

**Resposta 200:**
```json
{
  "message": "Senha redefinida com sucesso.",
  "whatsapp_sent": true,
  "temp_password": null,
  "whatsapp_error": null
}
```

### Importar vendedores (CSV/XLSX)

```http
POST /api/sellers/import_sellers/
Authorization: Bearer <token>
Content-Type: multipart/form-data

file: <arquivo.csv ou .xlsx>
```

### Detalhe do vendedor (manager)

```http
GET /api/manager/seller/{uuid}/?month=7&year=2026
Authorization: Bearer <token>
```

**Permissão:** MANAGER, ADMIN

**Resposta:**
```json
{
  "seller": { "uuid": "...", "name": "João Silva", "phone": "...", "is_active": true },
  "manual_total": 57990,
  "manual_sales": [
    { "uuid": "...", "amount": 3500, "sale_date": "2026-07-01" }
  ],
  "commissions": [
    {
      "status": "ABERTA",
      "commission_amount": 579.90,
      "period_status": "ABERTA",
      "operational_status": "PRONTO",
      "submitted_days_count": 1,
      "expected_working_days": 22
    }
  ],
  "evolution": [
    { "month": 6, "year": 2026, "total": 120000, "commission": 1200.00 }
  ],
  "days_in_month": 31,
  "submitted_days": [1, 2, 3],
  "today": "2026-07-01",
  "has_sale_today": true,
  "last_sale_date": "2026-07-01"
}
```

### Exportar relatórios (CSV / XLSX / PDF)

```http
GET /api/manager/seller/{uuid}/csv/?month=7&year=2026
GET /api/manager/seller/{uuid}/xlsx/?month=7&year=2026
GET /api/manager/seller/{uuid}/pdf/?month=7&year=2026
Authorization: Bearer <token>
```

---

## Sales (Vendas)

### Listar / Criar vendas

```http
GET /api/sales/
Authorization: Bearer <token>
```

```http
POST /api/sales/
Authorization: Bearer <token>
Content-Type: application/json

{
  "amount": 57990,
  "sale_date": "2026-07-01"
}
```

**Permissão:** SELLER (cria própria), MANAGER, ADMIN

**Regras:**
- `amount` em centavos (R$ 579,90 → 57990)
- Valor máximo: R$ 100.000 (10.000.000 centavos)
- Data não pode ser futura
- Apenas uma venda por vendedor por dia (origem MANUAL)
- Validação de comissão aberta (venda bloqueada se comissão do período estiver fechada/paga)

### Detalhe / Editar / Excluir venda

```http
GET /api/sales/{uuid}/
PUT /api/sales/{uuid}/
PATCH /api/sales/{uuid}/
DELETE /api/sales/{uuid}/
Authorization: Bearer <token>
```

**Permissão:** SELLER (própria, apenas hoje), MANAGER, ADMIN

### Vendas do vendedor autenticado

```http
GET /api/seller/sales/
Authorization: Bearer <token>
```

**Permissão:** SELLER

### Vendas do tenant (manager)

```http
GET /api/manager/sales/?seller={seller_uuid}&month=7&year=2026
Authorization: Bearer <token>
```

**Permissão:** MANAGER, ADMIN

---

## Links de Pagamento

### Listar / Criar links

```http
GET /api/seller/links/
Authorization: Bearer <token>
```

```http
POST /api/seller/links/
Authorization: Bearer <token>
Content-Type: application/json

{
  "customer_name": "Maria",
  "amount_cents": 15000,
  "installments": 1
}
```

**Permissão:** SELLER

**Rate limit:** 10/min (retorna 403 se excedido)

**Resposta 201:**
```json
{
  "uuid": "...",
  "gateway_url": "https://pay.pagar.me/links/pl_xxxx",
  "amount_cents": 15000,
  "status": "PENDING",
  "customer_name": "Maria",
  "created_at": "2026-07-01T16:00:00-03:00"
}
```

---

## Comissões

### Competências (CommissionPeriod)

#### Listar / Criar

```http
GET /api/commissions/periods/
Authorization: Bearer <token>
```

```http
POST /api/commissions/periods/
Authorization: Bearer <token>
Content-Type: application/json

{
  "month": 7,
  "year": 2026,
  "expected_working_days": 22,
  "notes": "Feriados dias 15 e 20"
}
```

**Permissão:** MANAGER, ADMIN

**Resposta GET (lista de competências com vendedores):**
```json
[
  {
    "uuid": "...",
    "month": 7,
    "year": 2026,
    "status": "ABERTA",
    "expected_working_days": 22,
    "notes": null,
    "seller_commissions": [
      {
        "id": 1,
        "seller_uuid": "...",
        "seller_name": "João Silva",
        "status": "ABERTA",
        "operational_status": "PRONTO",
        "total_sold_amount": 57990,
        "commission_rate": "0.0100",
        "commission_amount": 579.90,
        "submitted_days_count": 1,
        "expected_working_days": 22,
        "missing_days_count": 21,
        "is_editable": true
      }
    ],
    "is_current_month": true,
    "has_paid_commission": false
  }
]
```

#### Detalhe / Editar / Excluir

```http
GET /api/commissions/periods/{uuid}/
PATCH /api/commissions/periods/{uuid}/
DELETE /api/commissions/periods/{uuid}/
Authorization: Bearer <token>
```

#### Sincronizar vendedores

```http
POST /api/commissions/periods/{uuid}/sync/
Authorization: Bearer <token>
```

Recria SellerCommission para vendedores ativos ou com vendas manuais no período.

#### Fechar comissões

```http
POST /api/commissions/periods/{uuid}/close_sellers/
Authorization: Bearer <token>
Content-Type: application/json

{
  "seller_commission_ids": [1, 2, 3]
}
```

Congela os valores das comissões selecionadas (status → FECHADA). Usa `select_for_update` com lock ordenado para evitar deadlocks.

#### Reabrir comissões

```http
POST /api/commissions/periods/{uuid}/reopen_sellers/
Authorization: Bearer <token>
Content-Type: application/json

{
  "seller_commission_ids": [1, 2, 3],
  "reason": "Venda manual adicionada após fechamento"
}
```

Retorna comissões para ABERTA. Requer motivo. Impede reabertura de comissões já pagas.

#### Pagar comissões

```http
POST /api/commissions/periods/{uuid}/pay_sellers/
Authorization: Bearer <token>
Content-Type: application/json

{
  "seller_commission_ids": [1, 2, 3],
  "payment_date": "2026-07-01",
  "payment_method": "pix",
  "payment_notes": "Pagamento referente julho/2026"
}
```

**Permissão:** FINANCEIRO, ADMIN

Marca como PAGA e dispara notificação WhatsApp para cada vendedor.

#### Cancelar competência

```http
POST /api/commissions/periods/{uuid}/cancel/
Authorization: Bearer <token>
Content-Type: application/json

{
  "reason": "Periodo cancelado por erro administrativo"
}
```

### Filtro por status

```http
GET /api/manager/commissions/ABERTA/
Authorization: Bearer <token>
```

### Ranking

```http
GET /api/manager/ranking/?month=7&year=2026
Authorization: Bearer <token>
```

```http
GET /api/manager/ranking/annual/?year=2026
Authorization: Bearer <token>
```

### Dashboard

```http
GET /api/manager/dashboard/summary/?month=7&year=2026
Authorization: Bearer <token>
```

### Fila de pagamento (financeiro)

```http
GET /api/financial/payment-queue/
Authorization: Bearer <token>
```

**Permissão:** FINANCEIRO, ADMIN

### Exportar CSV financeiro

```http
GET /api/financial/commissions/{period_uuid}/csv/
Authorization: Bearer <token>
```

---

## Configuração

### Alterar senha (vendedor)

```http
POST /api/seller/change-password/
Authorization: Bearer <token>
Content-Type: application/json

{
  "current_password": "senha123",
  "new_password": "novaSenha@2026",
  "confirm_password": "novaSenha@2026"
}
```

**Regras:**
- Nova senha: mínimo 8 caracteres
- Deve ser diferente da atual

### Status do webhook

```http
GET /api/manager/webhook-status/
Authorization: Bearer <token>
```

**Permissão:** MANAGER, ADMIN

**Resposta:**
```json
{
  "webhook_url": "https://querolink.lojabibelo.com.br/api/webhooks/pagarme/bibelo/",
  "last_event": {
    "id": 123,
    "event_type": "charge.paid",
    "processed": true,
    "received_at": "2026-07-01T15:00:00-03:00"
  }
}
```

---

## Webhooks (externos)

### Pagar.me

```http
POST /api/webhooks/pagarme/{tenant_slug}/
Content-Type: application/json

{
  "id": "evt_xxxxxxxx",
  "type": "charge.paid",
  "data": { ... }
}
```

- `@csrf_exempt` (chamada externa do Pagar.me)
- Autenticação Basic Auth opcional (configurada por tenant)
- Dedup: eventos com mesmo `id` (`evt_*`) são ignorados após o primeiro processamento
- Sanitização: dados sensíveis removidos do payload antes de persistir (`scrub_payment_payload`)

### Evolution API (WhatsApp)

```http
POST /api/webhooks/evolution/{instance}/{tenant_uuid}/{token}/
Content-Type: application/json

{
  "event": "CONNECTION_UPDATE",
  "data": { ... }
}
```

- Protegido por HMAC (token no URL)

---

## Códigos de erro

| Código | Significado |
|--------|-------------|
| 200 | OK |
| 201 | Criado com sucesso |
| 204 | Excluído (sem corpo) |
| 400 | Erro de validação (corpo com detalhes) |
| 401 | Não autenticado (token ausente/inválido) |
| 403 | Proibido (sem permissão para a ação) |
| 404 | Recurso não encontrado |
| 405 | Método não permitido |
| 429 | Rate limit excedido (muitas requisições) |
| 500 | Erro interno do servidor |

### Exemplo de erro 400:
```json
{
  "amount": ["Valor maximo e R$ 100.000,00."],
  "sale_date": ["So e permitida uma venda manual por dia."]
}
```

### Exemplo de erro 403:
```json
{
  "detail": "Voce nao tem permissao para realizar esta acao."
}
```

---

## Rate Limits

| Endpoint | Limite | Escopo |
|----------|--------|--------|
| `POST /api/auth/login/` | 5/min | IP + username |
| `POST /api/seller/links/` | 10/min | IP |
| `POST /api/seller/change-password/` | — | Usuário autenticado |
| Demais endpoints | 1000/h (user) / 100/h (anon) | Usuário / IP |
