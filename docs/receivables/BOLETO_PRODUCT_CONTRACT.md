# Contrato de produto do boleto

Data de referência: 20/07/2026, America/Sao_Paulo.

## Limites do domínio

- `Order`/`PaymentLink` e `Boleto` continuam aggregates independentes.
- A Central de Cobranças é uma fachada de consulta; não cria um terceiro aggregate.
- Este trabalho não altera PIX, cartão, planos, preços, providers ou migrations.

## Capacidades

- Histórico: autenticado, mesmo tenant e escopo do papel; independe de plano, flag de emissão e configuração do provider.
- Emissão: ADMIN, MANAGER ou SELLER; exige entitlement, flag operacional e provider válido. SELLER é derivado da sessão.
- Cancelamento: somente ADMIN e MANAGER, para boleto cancelável do mesmo tenant.
- Allocation: ADMIN, MANAGER e FINANCEIRO; SELLER não acessa operações financeiras.
- FINANCEIRO consulta histórico e allocations, mas não emite nem cancela.

## Contrato da emissão

- O pagador é identificado por CPF/CNPJ exato dentro do tenant; nome semelhante nunca autoriza merge.
- CNPJ e CEP são assistências opcionais. Falha externa não bloqueia preenchimento manual e nunca sobrescreve silenciosamente dados já digitados.
- Valores trafegam em centavos; vencimento permitido entre amanhã e 180 dias.
- A chave de idempotência é um UUID estável por tentativa lógica e deve ser reutilizada em retry.
- O backend é autoridade para tenant, papel e vendedor.
- Sucesso deve devolver UUID local, status, URL real do boleto, linha digitável e código de barras reais. IDs do provider não são URLs.
- Erros públicos devem ser estáveis e não expor mensagens internas do provider ou PII.

## Histórico e resultado

- Desativar emissão não oculta boletos existentes.
- A tela de resultado permite abrir/baixar o boleto e copiar a linha digitável quando esses dados existirem.
- Ausência temporária dos dados do provider é apresentada como processamento/reconciliação, sem inventar valores.

## Decisão de schema

O contrato exige dois valores distintos: `digitable_line` e `barcode`. O modelo atual possui apenas `Boleto.provider_barcode`. O adapter Pagar.me usa `transaction.line` e, na ausência, `transaction.barcode`, gravando ambos no mesmo atributo `ProviderResult.barcode`. Assim, o schema atual não consegue preservar simultaneamente os dois valores.

O responsável autorizou explicitamente a migration progressiva mínima `receivables.0002`, que adiciona `provider_digitable_line` sem alterar a migration estabilizada `0001`. Registros anteriores preservam `provider_barcode`; a linha fica vazia até ser obtida por criação ou reconciliação, evitando inferência incorreta sobre dados legados.
