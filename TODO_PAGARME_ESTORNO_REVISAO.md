# TODO — Revisão futura do estorno Pagar.me

Data: 15/07/2026

## Estado

- Branch de integração atual testada: `querolink-v2`
- Merge mais recente relacionado: PR #29
- SHA após merge: `306c6d7`

## Problema

O estorno ainda não funciona em produção/deploy mesmo após corrigir o endpoint usado pelo cliente Pagar.me.

Erro original observado antes do PR #29:

```text
Erro ao estornar: Erro na comunicacao com Pagar.me: 404 Client Error: Not Found for url: https://api.pagar.me/core/v5/charges/ch_ydaVW4ju5Aueq9l8/partial
```

Após o PR #29, o código deixou de chamar `/partial` e passou a usar `/charges/{charge_id}/cancel`, mas o fluxo real ainda precisa ser revalidado com pagamento/link real.

## Hipóteses para próxima revisão

- Confirmar na resposta real do Pagar.me qual endpoint e payload corretos para estorno total/parcial.
- Verificar se o `gateway_transaction_id` salvo é sempre o `charge.id` esperado pelo endpoint de cancelamento.
- Confirmar se o estorno parcial exige campos adicionais além de `amount`.
- Salvar o retorno do cancelamento para auditoria/comprovante.
- Exibir mensagem amigável quando o Pagar.me recusar o estorno.
- Avaliar botão/download de comprovante de estorno na tela do link.

## Fora do próximo Prompt 53

Não misturar esta revisão com fechamento, contadores e lembretes de justificativas.
