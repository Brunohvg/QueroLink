# PROMPT 54 — PERÍODOS LIVRES COM PRESERVAÇÃO DO CICLO ATUAL

Siga `AGENTS.md`.

## Natureza

Este prompt é estrutural e deve ser executado em branch pequena, PR draft e sem merge automático.

Base:

```text
querolink-v2
```

Branch:

```text
fix/free-periods-market-cycle-safe
```

## Objetivo de produto

Preparar o Mérito para lojistas além da Bibelô, sem quebrar o modelo atual por ciclo.

O sistema deve continuar atendendo perfeitamente ciclos como:

```text
21/06/2026 a 20/07/2026
```

Mas também deve permitir modelos legítimos de mercado:

- 01/07/2026 a 15/07/2026;
- 16/07/2026 a 31/07/2026;
- períodos semanais;
- períodos de campanha;
- períodos de 1 dia;
- períodos maiores que 62 dias, quando o lojista optar por isso;
- gaps entre períodos;
- múltiplas competências no mesmo mês/ano, desde que não se sobreponham.

## Regra central

`start_date` e `end_date` são a fonte da verdade operacional.

`month` e `year` são apenas metadados/compatibilidade para rotas antigas, labels e filtros legados.

`period_start_day` é apenas sugestão/editável. Ele não controla operação nem deve impedir período manual.

## Regressão obrigatória Bibelô

Garantir que o ciclo atual continue funcionando sem alteração semântica:

- competência 21/06–20/07 continua possível;
- vendas de 21/06–20/07 continuam vinculadas por range;
- fechamento mantém os mesmos totais;
- ranking por competência mantém os mesmos totais;
- export contábil por período mantém os mesmos dados;
- importação continua resolvendo competência por `sale_date` dentro do range;
- lembrete diário continua usando competência atual por range;
- `period_start_day=21` continua sugerindo 21–20, mas a sugestão é editável.

## Fase 1 — auditoria obrigatória

Antes de alterar, executar:

```bash
grep -RIn "period_start_day\|suggest_period_range\|sale_date__month\|sale_date__year\|62" app templates
```

Criar:

```text
AUDITORIA_PERIODOS_LIVRES_ANTES.md
```

Classificar cada ocorrência relevante:

- operacional indevido;
- metadado permitido;
- rota legada;
- migration histórica;
- teste;
- código morto;
- fora de escopo.

## Fase 2 — implementação mínima

Implementar somente o necessário para períodos livres:

- remover a trava operacional por `(tenant, month, year)` para competências não canceladas;
- manter bloqueio de overlap por `tenant + start_date/end_date`;
- permitir gaps;
- permitir múltiplas competências no mesmo mês/ano se não houver overlap;
- remover bloqueio fixo de 62 dias para competências;
- manter criação manual por datas;
- manter sugestão opcional e editável baseada em `period_start_day`;
- manter rotas antigas por mês/ano como compatibilidade explícita;
- não editar migrations históricas;
- criar migration nova somente se indispensável.

## Testes obrigatórios

Cobrir:

- ciclo Bibelô 21/06–20/07;
- período 01/07–15/07;
- período 16/07–31/07 no mesmo mês/ano;
- período de 1 dia;
- período maior que 62 dias;
- overlap bloqueado;
- gap permitido;
- cancelada não bloqueia recriação;
- venda órfã;
- mesmo mês/ano sem overlap;
- dashboard por UUID/range;
- ranking por UUID/range;
- exports por período;
- tenant isolation;
- importação vinculando por range;
- lembrete diário usando range.

## Gates

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py migrate --plan
python manage.py test app.apps.api.tests.test_commissions -v 2
python manage.py test app.apps.api.tests.test_competencia_first -v 2
python manage.py test app.apps.dashboard.tests.test_post_merge_stability -v 2
python manage.py test app.apps.commissions.tests -v 2
python manage.py test -v 2
DJANGO_SETTINGS_MODULE=app.config.settings.production python manage.py check --deploy
npm ci
npm run build:css
docker compose config
git diff --check
```

## Saída

```text
PRONTO PARA AUDITORIA
```

ou:

```text
BLOCKERS RESTANTES
```
