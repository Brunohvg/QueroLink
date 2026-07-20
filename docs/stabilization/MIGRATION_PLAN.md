# Plano de migrations para PostgreSQL novo

## Decisão

Preservar todas as migrations legadas. Manter `accounts.0024_tenant_receivables_enabled`: é um `AddField` normal, determinístico e foi introduzido como dependência explícita da fundação.

Os apps `receivables` e `customers` foram criados integralmente nesta branch, ainda não publicada em produção e sem dados a preservar. Suas cadeias serão reconstruídas.

## Receivables

Remover `0001` a `0008` e gerar uma única `0001_initial` a partir dos models finais. Ela deverá conter:

- `Boleto`, incluindo PII criptografada, provider IDs, URL, barcode, documentos e lifecycle;
- `IntegrationOutbox`;
- `ReceivableAllocation`;
- `CommissionImpactReview`;
- `ReceivableNotificationDelivery`;
- PKs UUID, FKs, `on_delete`, defaults, nulabilidade, índices e constraints finais;
- dependências em `accounts.0024`, `sales.0007`, `commissions.0007`, `sellers.0013` e `swappable_dependency(AUTH_USER_MODEL)` conforme relações efetivas.

Não haverá `RunSQL`, introspecção, `SeparateDatabaseAndState`, fallback de DDL ou FK física hardcoded.

## Customers

Remover `0001` e `0002` e gerar `0001_initial` final com:

- `Customer`, `CustomerActivity`, `CustomerIdentityConflict`;
- PII criptografada e hashes;
- choices finais, índices e três constraints finais;
- dependências mínimas e reais.

## Validação

1. gerar migrations via Django;
2. revisar diff e estado manualmente;
3. `sqlmigrate` das duas `0001`;
4. migrar banco PostgreSQL descartável vazio;
5. repetir migrate e comprovar no-op;
6. `makemigrations --check --dry-run`;
7. consultar `pg_catalog`/`information_schema` para PK/FK/índices/constraints/null/default;
8. comparar models e migration state;
9. executar suíte completa serial e paralela.

Nenhum banco externo será apagado e nenhum registro de `django_migrations` será manipulado.

## Resultado executado

O plano foi cumprido. O banco descartável PostgreSQL 17 migrou do vazio ao latest, `sqlmigrate` foi revisado, a segunda execução foi no-op e o schema físico confirmou PKs, FKs, uniques, checks e índices. Consulte `CANONICAL_SCHEMA.md` para os números e o grafo final.
