# AUDITORIA PERIODOS LIVRES — ANTES

Branch auditada: `fix/free-periods-market-cycle-safe`

Base: `querolink-v2`

## Comando executado

```bash
grep -RIn "period_start_day\|suggest_period_range\|sale_date__month\|sale_date__year\|62" app templates
```

## Achados

### Operacional indevido

#### `app/apps/commissions/models.py`

- `CommissionPeriod.Meta.constraints`
- Achado: constraint parcial `unique_period_per_tenant` em `(tenant, month, year)` para períodos não cancelados.
- Risco: impede períodos livres legítimos no mesmo mês/ano, por exemplo `01/07–15/07` e `16/07–31/07`.
- Correção mínima: remover a constraint por migration nova e manter overlap por `start_date/end_date`.

#### `app/apps/api/serializers.py`

- `CommissionPeriodCreateSerializer.validate`
- Achado: bloqueio manual de `(tenant, month, year)` não cancelado.
- Risco: mesmo removendo a constraint, a API continuaria impedindo períodos livres no mesmo mês/ano.
- Correção mínima: remover validação por mês/ano e manter validação de overlap.

#### `app/apps/commissions/models.py`

- `CommissionPeriod.clean`
- Achado: limite fixo de 62 dias.
- Risco: impede períodos longos legítimos de campanha/temporada.
- Correção mínima: remover limite fixo; manter validação de data inicial/final e overlap.

#### `app/apps/api/serializers.py`

- `CommissionPeriodCreateSerializer.validate`
- Achado: limite fixo de 62 dias.
- Risco: API impede período maior que 62 dias.
- Correção mínima: remover limite fixo.

#### `templates/dashboard/gestor/fechamento.html`

- Campo `createExpectedDays` com `max="62"`.
- Risco: UI impede informar dias esperados acima de 62.
- Correção mínima: remover `max="62"` do input.

### Metadado permitido / sugestão editável

#### `app/apps/commissions/services.py`

- `suggest_period_range`
- Usa `tenant.period_start_day` apenas para sugerir datas.
- Classificação: permitido.
- Observação: deve continuar funcionando para Bibelô (`period_start_day=21`) como sugestão 21–20.

#### `app/apps/api/views.py`

- Endpoint `/api/commissions/periods/suggest/`
- Retorna sugestão baseada em `period_start_day`.
- Classificação: permitido, desde que as datas continuem editáveis na criação.

#### `templates/dashboard/gestor/configuracoes.html`

- Campo de configuração `period_start_day`.
- Classificação: permitido como preferência de sugestão.

#### `app/apps/dashboard/desktop_views.py`

- Salvamento de `period_start_day`.
- Classificação: permitido como configuração de sugestão.

### Rota legada / compatibilidade

#### `app/apps/commissions/services.py`

- `get_period_by_legacy_label(tenant, month, year)`
- `legacy_month_range(month, year)`
- `get_manual_sales_total(seller, month, year)`
- `calculate_estimated_commission(seller, month, year)`
- `get_dashboard_data(tenant, month, year)`
- `get_missing_days_before_today(seller, month, year)`
- Classificação: rota legada/compatibilidade.
- Observação: manter, mas novas rotas devem preferir UUID/range.

#### `app/apps/api/views.py`

- Filtros de listagem por `month`/`year`.
- Relatórios antigos por path `year/month`.
- Classificação: rota legada/compatibilidade.
- Observação: não remover neste PR; garantir que rotas por período/UUID continuem fonte preferida.

#### `app/apps/sellers/models.py`

- `SellerGoal.progress_percent` usa `sale_date__year` e `sale_date__month`.
- Classificação: compatibilidade de meta mensal, fora da regra de competência.
- Observação: não alterar neste PR para evitar mudança de produto em metas.

### Migration histórica

- `app/apps/accounts/migrations/0023_tenant_period_start_day.py`
- `app/apps/commissions/migrations/0001_initial.py`
- `app/apps/commissions/migrations/0002_alter_commissionperiod_unique_together_and_more.py`
- `app/apps/commissions/migrations/0006_remove_commissionperiod_unique_period_per_tenant_and_more.py`
- `app/apps/sellers/migrations/0013_sellerdayjustification.py`
- Classificação: migration histórica.
- Regra: não editar.

### Testes

- Ocorrências em `app/apps/*/tests/*.py`.
- Classificação: testes existentes.
- Ação: ajustar/adicionar testes somente onde a nova regra exige.

### Fora de escopo

- `app/apps/freight/services.py`: faixa CEP contendo número `62.0`.
- Templates com classes CSS ou SVG contendo `62`.
- Classificação: fora de escopo.

## Conclusão

O sistema já usa `start_date/end_date` em boa parte da operação e preserva bem o ciclo atual da Bibelô, mas ainda não suporta período livre real por causa de:

1. constraint `(tenant, month, year)` em competências não canceladas;
2. validação manual equivalente no serializer;
3. limite fixo de 62 dias no model/API/UI.

Implementação mínima recomendada: remover esses bloqueios e reforçar testes de range, mantendo `period_start_day` apenas como sugestão editável e mantendo rotas antigas como compatibilidade explícita.
