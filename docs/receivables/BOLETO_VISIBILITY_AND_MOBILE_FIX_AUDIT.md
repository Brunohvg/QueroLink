# Auditoria — Hotfix Boleto Visibility e Mobile Links 500

## Causa do erro 500 em `/dashboard/mobile/links/`

**Arquivo:** `app/apps/dashboard/mobile_views.py:1095`

```python
orders = Order.objects.filter(...)
```

A view `mobile_links` usava `Order` sem import válido (`NameError: name 'Order' is not defined`).
A view `mobile_cobrancas` (linha 1202) já possuía o import local, mas `mobile_links` não.

**Correção:** Adicionado `from app.apps.orders.models import Order` na linha 1095.

## Causa da ausência de "Gerar boleto" no desktop

**Diagnóstico da capability:**

A view `gestor_cobrancas` (`desktop_views.py:887`) usava:

```python
can_create_boletos = tenant_has_feature(tenant, 'boletos')
```

`tenant_has_feature()` verifica apenas:
1. Plano inclui feature `boletos`
2. `tenant.receivables_enabled` é True

Porém, a capability real de criação de boleto (`can_create_receivable`) também exige:
3. `tenant.pagarme_configured` (provider Pagar.me configurado)
4. Papel do usuário (ADMIN, MANAGER ou SELLER)

Além disso, as views mobile (`mobile_cobrancas`, `mobile_boleto_new`) e os endpoints de API já usavam `can_create_receivable()`, criando uma inconsistência onde o desktop mostrava a opção mas a API/endpoint bloqueava, ou vice-versa.

**Causa provável em produção:** Se o tenant tiver `receivables_enabled=False` ou estiver no plano STARTER, `tenant_has_feature('boletos')` retorna False e a opção não aparece — isso é correto para bloqueio. Porém, mesmo com tudo configurado (PRO + Pagar.me), o desktop usava uma verificação mais fraca que a API, causando falsos positivos ou inconsistências.

**Correção:** Substituído `tenant_has_feature(tenant, 'boletos')` por `can_create_receivable(request.user, tenant)` em `desktop_views.py:887`.

## Causa da ausência no mobile

**Template:** `templates/mobile/cobrancas.html:23`

O botão "Emitir boleto" usava `{% if has_boletos %}` em vez de `{% if can_create_boletos %}`.

`has_boletos` é `bool(can_create_boletos or boletos_data)` — ou seja, aparecia mesmo quando o usuário não podia criar, desde que houvesse boletos históricos. Além disso, desaparecia quando `can_create_boletos=True` mas não havia boletos históricos.

**Correção:** Substituído `has_boletos` por `can_create_boletos` na condição do template.

## Regras corretas de criação de boleto

A capability `can_create_receivable(user, tenant)` (`accounts/models.py:220`) exige:

1. Usuário autenticado (`can_view_receivables_history`)
2. Papel em ADMIN, MANAGER ou SELLER
3. Plano inclui `boletos` (`tenant_has_feature`)
4. `tenant.receivables_enabled` é True
5. `tenant.pagarme_configured` é True (Pagar.me com API key válida)

**WhatsApp NÃO é exigido para emissão de boleto.** A presença ou ausência do WhatsApp afeta apenas notificações/lembretes.

## Arquivos alterados

| Arquivo | Alteração |
|---------|-----------|
| `app/apps/dashboard/mobile_views.py:1095` | Adicionado `from app.apps.orders.models import Order` |
| `app/apps/dashboard/desktop_views.py:887` | Substituído `tenant_has_feature` por `can_create_receivable` |
| `templates/mobile/cobrancas.html:23` | Substituído `has_boletos` por `can_create_boletos` |
| `app/apps/dashboard/tests/test_mobile_links_and_boleto_visibility.py` | Novo arquivo — 23 testes de regressão |

## Testes

### Testes criados: 23

- **Mobile links regression (7):** 200 com links, 200 sem links, tenant isolation, seller isolation, unauthenticated redirect, sem seller profile redirect
- **Desktop boleto visibility (6):** capability=True, receivables=False, STARTER plan, Pagar.me not configured, history visible when blocked, manager can create
- **Mobile boleto visibility (6):** capability=True, capability=False, new page 200, new page redirect disabled, list 200, list shows boleto, tenant isolation
- **PagarmeWithoutWhatsApp (2):** manager + gestor, seller + mobile
- **Security (2):** seller não vê links de outro, seller não vê boletos de outro

### Resultados

- Testes focados (23): OK (100%)
- Testes de charge_center existentes (4): OK
- Testes de receivables existentes (134): OK
- Suíte completa serial (994): OK
- Suíte completa paralela: 148 erros pré-existentes (`provider_digitable_line` migration sync — não relacionado a este hotfix)

## Migrations

Nenhuma migration criada. `makemigrations --check --dry-run` limpo.

## Ambiente de diagnóstico

Não foi possível executar diagnóstico no tenant Bibelô diretamente (sem acesso ao banco de produção). A correção aplica a capability `can_create_receivable()` que unifica corretamente todos os checks.

## Customer Ledger

NÃO ALTERADO. Nenhum arquivo da Central de Clientes foi modificado.

## Gates

- `python manage.py check`: 0 issues
- `python manage.py check --deploy`: pre-existing warnings (drf_spectacular, security) — fora do escopo
- `python manage.py makemigrations --check --dry-run`: No changes
- `python manage.py migrate --plan`: clean, no new migrations
- `grep -rn "json.dumps\|_json.dumps" app/apps/dashboard/*.py | grep -v test`: none found
- `git diff --check`: clean
