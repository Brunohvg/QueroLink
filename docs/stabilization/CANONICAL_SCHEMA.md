# Schema canônico para banco novo

## Decisão arquitetural

`Order`/`PaymentLink` e `Boleto`/`Receivable` permanecem aggregates distintos. A Central de Cobranças é uma façade de consulta e navegação; não existe tabela polimórfica de cobrança.

`accounts.0024_tenant_receivables_enabled` permanece como `AddField` válido. Somente os apps inéditos `customers` e `receivables` tiveram suas cadeias reconstruídas.

## Grafo final dos apps reconstruídos

```text
accounts.0024 ───────────────┬── customers.0001_initial
                             └── receivables.0001_initial
AUTH_USER_MODEL ───────────────── receivables.0001_initial
sellers.0013 ──────────────────── receivables.0001_initial
sales.0007 ────────────────────── receivables.0001_initial
commissions.0007 ──────────────── receivables.0001_initial
```

## Entidades finais

- `customers`: `Customer`, `CustomerActivity`, `CustomerIdentityConflict`.
- `receivables`: `Boleto`, `IntegrationOutbox`, `ReceivableAllocation`, `CommissionImpactReview`, `ReceivableNotificationDelivery`.
- PKs de domínio são UUID; relações para usuário usam `settings.AUTH_USER_MODEL` e `swappable_dependency`.
- `Boleto` contém payer, endereço, valores em centavos, lifecycle, provider/order/charge, URL, barcode e documentos privados.
- Outbox e deliveries têm chaves idempotentes por tenant.
- Allocation é única por boleto e referencia venda, tenant e usuário alocador.

## Operações removidas

Foram eliminados da cadeia final de `receivables`: `RunSQL`, DDL dinâmico, introspecção/autocura, `SeparateDatabaseAndState` e referência física a `accounts_user(uuid)`. `customers.0002` vazio também foi removido.

## Prova física em PostgreSQL 17

- banco descartável: `querolink_v2_stabilization_test`;
- instalação limpa: todas as migrations aplicadas com sucesso;
- segunda execução: `No migrations to apply`;
- tabelas dos dois apps: 8;
- índices físicos: 46;
- PK/FK/unique/check relevantes enumerados: 47;
- `sqlmigrate`: 102 linhas para `receivables.0001` e 38 para `customers.0001`;
- `makemigrations --check --dry-run`: `No changes detected`.
- query principal da Central (links, pagamentos prefetched e boletos): 3 queries no cenário de regressão coberto.

As FKs físicas geradas apontam, entre outras, para `accounts_user.id`, `accounts_tenant.uuid`, `sellers_seller.uuid`, `sales_sale.id` e tabelas de commissions, conforme o tipo real resolvido pelo estado do Django.
