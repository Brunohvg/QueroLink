# PRD — QueroLink Sistema de Comissões

## 1. Visão Geral

QueroLink é um sistema multi-tenant de gestão de comissões para vendedores de lojas.
O tenant principal é a Loja Bibelô, com 16 vendedores reais.

## 2. Arquitetura

- Django 5.1 + SQLite (dev) / PostgreSQL (prod)
- Celery + Redis para tarefas assíncronas
- Multi-tenant via model Tenant, com isolamento lógico nos models
- Custom User model com roles: ADMIN, MANAGER, FINANCEIRO, SELLER

## 3. Apps

| App | Função |
|-----|--------|
| accounts | Tenant, User customizado |
| sellers | Seller (vendedor) |
| orders | Order, PaymentLink |
| payments | Payment (transações gateway) |
| sales | Sales (vendas lançadas) |
| commissions | CommissionPeriod, SellerCommission |
| webhooks | WebhookEvent (Pagar.me) |
| notifications | Templates de notificação |
| analytics | Analytics de cliques |
| audit | Auditoria |
| dashboard | Dashboard do gestor |

## 4. Models

### 4.1 Models aprovados (já existentes)

#### Sale
- `uuid` UUIDField PK
- `tenant` FK → Tenant (related_name='sales')
- `seller` FK → Seller (related_name='sales')
- `order` OneToOneField → Order (nullable)
- `origin` ChoiceField (LINK/MANUAL)
- `amount` PositiveIntegerField (centavos)
- `sale_date` DateField
- `notes` CharField (nullable)
- `created_by` FK → User (nullable, related_name='sales_lancadas')
- `created_at` DateTimeField auto

#### CommissionPeriod
- `uuid` UUIDField PK
- `tenant` FK → Tenant (related_name='commission_periods')
- `month`, `year` PositiveSmallIntegerField
- `status` ChoiceField (ABERTA/EM_CONFERENCIA/ENVIADA_FINANCEIRO/APROVADA/PAGA)
- `sent_to_financial_at`, `approved_at`, `paid_at` DateTimeField (nullable)
- `unique_together` (tenant, month, year)

#### SellerCommission
- `period` FK → CommissionPeriod (related_name='seller_commissions')
- `seller` FK → Seller (related_name='commissions')
- `total_sold_amount` PositiveIntegerField (centavos)
- `commission_rate` DecimalField(max_digits=5, decimal_places=4)
- `commission_amount` PositiveIntegerField (centavos)
- `payment_date`, `payment_method`, `payment_notes` (nullable)
- `unique_together` (period, seller)
- Método `recalculate()`: soma Sales do período, aplica commission_rate

### 4.2 Vínculo Seller ↔ User (IMPLEMENTAR NESTE LOTE)

#### Seller.user

```python
user = models.OneToOneField('accounts.User', on_delete=models.CASCADE, related_name='seller_profile')
```

Regra de migration:
1. A primeira migration cria o campo temporariamente como `null=True, blank=True`, mas já com
   `on_delete=models.CASCADE` e `related_name='seller_profile'` (NUNCA usar `SET_NULL`, porque
   o campo final será obrigatório e isso causa conflito técnico).
2. A data migration cria/víncula `User` para cada `Seller` existente sem usuário.
3. Depois da data migration, alterar o model para `null=False` e gerar uma segunda migration
   tornando o campo obrigatório.

#### Seller.commission_rate

`commission_rate = models.DecimalField(max_digits=5, decimal_places=4, default=0.01)`

- No MVP, ao criar um vendedor novo, o valor inicial deve copiar `Tenant.default_commission_rate`.
- Como o campo não é nulo, ele guarda a taxa efetiva do vendedor (não funciona como "fallback"
  — se um dia o tenant alterar `default_commission_rate`, o seller permanece com o valor que
  recebeu no momento da criação).

#### Tenant.default_commission_rate

`default_commission_rate = models.DecimalField(max_digits=5, decimal_places=4, default=0.01)`

- Taxa padrão do tenant, usada como valor inicial para novos `Seller` no momento do cadastro.

#### Regra crítica de colisão de nomes
Os seguintes `related_name` JÁ existem vindos de outros models para Seller:
- `sales` (de Sale.seller)
- `orders` (de Order.seller)
- `commissions` (de SellerCommission.seller)

NENHUMA `@property` nova no model Seller pode usar esses nomes.

### 4.3 Migração de dados

A data migration deve:
1. Para cada Seller existente sem `user` vinculado:
   - Gerar um `username` único: `slugify(name)`, com sufixo `-2`, `-3`... se duplicado
   - Tratar colisões como "Maria"/"Marcila" (slugs diferentes: `maria` vs `marcila` — ok) e "Léo"/"Leonardo Bruno" (`leo` vs `leonardo-bruno` — ok)
   - Gerar senha temporária aleatória: `get_random_string(12)` com letras + dígitos
   - Criar `User` com `role=SELLER` e `tenant=seller.tenant`
   - Vincular `user` ao `Seller`
   - Imprimir username e senha no console (única exposição da senha em texto plano — para repasse manual)
2. A senha NÃO pode ser salva em arquivo do repositório

### 4.4 Comando reset_seller_password

`python manage.py reset_seller_password <seller_uuid>`
- Busca Seller pelo UUID
- Gera nova senha temporária via `get_random_string(12)`
- Aplica com `user.set_password()` + `user.save()`
- Imprime a nova senha no terminal
- Cobre o caso "esqueci minha senha" sem depender de e-mail

## 5. Fluxos

### 5.1 Autenticação do vendedor
- Login via username (slug do nome) + senha temporária
- Reset de senha feito pelo gestor via comando de management
- Não há fluxo de e-mail para reset de senha

## 6. API (Lote 2)
A implementar futuramente.

## 7. Telas (Lote 1.5 e posteriores)
A implementar futuramente.

## 8. PWA (Lote 3+)
A implementar futuramente.

## 9. Webhooks
Bug conhecido de correlação em `webhooks/tasks.py` — fora de escopo, não corrigir.

## 10. Testes

### 10.1 Testes de model (este lote)
1. **Isolamento multi-tenant:** Seller do tenant A não pode ser vinculado a Sale do tenant B
2. **Regressão `seller.sales.all()`:** Acesso ao related_name não dispara exceção nem recursão
3. **Data migration:** Cria Users corretamente sem duplicar usernames
4. **`SellerCommission.recalculate()`:** Soma correta das Sales do período
5. **`reset_seller_password`:** Gera senha válida, login funciona com nova senha

### 10.2 Testes de API (Lote 2)
A implementar futuramente.
