# Estado da importação existente

## Mantido neste PR

A importação CSV existente e a matriz XLSX introduzida pelo PR #65 foram preservadas, sem expansão funcional. O modelo XLSX continua com as abas `IMPORTACAO`, `OBSERVACOES`, `INSTRUCOES` e `_META` oculta.

Foram estabilizados somente defeitos do contrato já entregue:

- bytes XLSX são abertos por `BytesIO` tanto na detecção quanto no parser;
- datas numéricas do Excel são normalizadas para `date`, não `datetime`;
- falha interna ao gerar modelo não é exposta ao usuário;
- detecção e parse do arquivo gerado têm teste de regressão;
- limite de linhas, limite de vendedores, tenant e UUIDs de `_META` permanecem.

## Não executado

Não houve nova expansão XLSX, novas colunas, novo workflow, novo módulo de clientes ou alteração comercial. Observações continuam sendo uma aba informativa do modelo; ampliar sua projeção para vendas fica fora deste PR.
