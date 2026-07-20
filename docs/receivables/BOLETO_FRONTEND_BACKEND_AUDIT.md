# Auditoria frontend/backend do boleto

## Baseline

- Base: `origin/querolink-v2` em `9e60c43333616ef373a5c7269a1fe8e6b2872c13`.
- Pré-requisito: PR #66 integrado em 20/07/2026.
- Branch: `feat/align-boleto-product-frontend-backend`.
- Working tree estava limpo antes desta documentação.
- Suíte baseline: 958 testes, 126,708 s, sem falhas ou erros, PostgreSQL descartável preservado com `--keepdb`.

## Estado real

| Área | Estado observado | Gap para o contrato |
|---|---|---|
| Domínio | `Boleto` separado de `Order`/`PaymentLink`; Central atua como fachada | correto |
| Histórico web | views desktop/mobile usam capacidade histórica | permanece visível sem plano, flag ou provider |
| Histórico API | GET de lista/detalhe usa capacidade histórica e escopo tenant/seller | alinhado |
| Emissão | API deriva SELLER da sessão e valida seller do tenant para gestor | plano, flag, papel e provider são verificados centralmente |
| Capacidades | políticas distintas para histórico, emissão, cancelamento, allocation e sellers | alinhado |
| FINANCEIRO | API permite allocations; emissão é negada | histórico fica condicionado ao feature flag; stats não seguem integralmente a matriz |
| CNPJ | BrasilAPI, cache 24 h, timeout 10 s, resposta interna normalizada | valida somente comprimento antes da consulta; desktop engole erro; mobile não consulta |
| CEP | BrasilAPI, cache 24 h, timeout 10 s, resposta interna normalizada | desktop engole erro; mobile não consulta; ausência externa é indistinguível de indisponibilidade |
| Customer Ledger | lookup exato por hash e tenant retorna somente dados necessários à emissão | não altera cadastro silenciosamente; endereço continua sendo snapshot do boleto |
| Desktop | autocomplete defensivo, reaproveitamento de cliente, feedback acessível e POST funcional | fluxo visual por etapas/revisão permanece melhoria posterior |
| Mobile/PWA | mesmo helper de lookup/idempotência, CNPJ/CEP e cliente existente | resultado é destacado na lista; tela dedicada permanece melhoria posterior |
| Idempotência | backend possui unique por tenant e frontend usa UUID estável durante a tentativa | alinhado para retry e duplo clique |
| Provider | URL, linha digitável e barcode são persistidos separadamente | alinhado |
| API | lista, detalhe/cancelamento, stats, allocation e review existem | erros não têm envelope/códigos uniformes; `BoletoServiceError` presume sempre `boleto` |
| Detalhe | expõe URL e `provider_barcode` | não distingue linha digitável de barcode; dados do pagador não integram o contrato de detalhe |
| Observabilidade | lifecycle, erro sanitizado, outbox e reconciliação existem | falta telemetria específica de lookups e etapas do frontend |

## Achados prioritários

1. **Resolvido:** linha digitável e código de barras agora possuem campos e contratos separados.
2. **Resolvido:** histórico independe da capacidade de emissão.
3. **Resolvido:** mobile e desktop compartilham as primitivas críticas de lookup e idempotência.
4. **Resolvido:** cliente é localizado por documento exato, tenant e hash, sem merge ou atualização silenciosa.
5. **Resolvido:** capacidades operacionais foram separadas e aplicadas nas entradas principais.
6. **Resolvido:** CNPJ/CEP têm feedback e preservam valores já digitados.
7. **Parcial:** erros de emissão ganharam códigos estáveis; validações DRF continuam no formato padrão para compatibilidade.
8. **Pendente não bloqueante:** formulário visual por etapas e tela mobile dedicada de sucesso podem ser evoluídos sem alterar o contrato estabilizado.

## Decisão da auditoria

O adendo é útil e está alinhado ao produto. A implementação foi inicialmente interrompida no ponto de parada obrigatório e retomada somente após autorização explícita do responsável para a migration progressiva mínima. A `receivables.0001` estabilizada permanece intacta.

## Evidências finais

- 960 testes completos: OK.
- 45 testes focados de API, views, provider e services: OK.
- 10 testes focados de notificações: OK.
- instalação limpa no PostgreSQL `test_querolink_boleto_alignment_clean_20260720_02`: OK.
- segunda execução de `migrate --noinput`: nenhuma migration a aplicar.
- `check`, `makemigrations --check --dry-run` e `git diff --check`: OK.
