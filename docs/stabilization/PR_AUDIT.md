# Auditoria forense dos PRs fechados

Intervalo: 15/07/2026 00:00 a 20/07/2026 23:59, America/Sao_Paulo. Fonte: GitHub CLI/API, com leitura de metadados, corpos, commits, arquivos, reviews/comentários e comparação dos patches/commits. Foram encontrados 39 PRs (#27–#65), todos mesclados em `querolink-v2`; nenhum fechado sem merge e nenhuma outra base no intervalo.

## Resultado individual

| PR | Área e alteração | Contrato/regressão | Situação e ação |
|---:|---|---|---|
| #27 | Webhook Pagar.me, orders/payments, notificações e dashboards | Tornou pagamento idempotente, mas retirou `Sale LINK` operacional | Manter política confirmada: link é cobrança, não venda contábil automática |
| #28 | Dashboard e export contábil | Excluiu LINK de export contábil | Manter; cobrir regressão |
| #29 | Refund e webhooks estrangeiros | Corrigiu endpoint/valor e correlação tenant | Manter |
| #30 | Justificativas, fechamento e reminders | Centralizou status de dia e reduziu N+1 | Manter |
| #31 | Períodos livres/comissões | Removeu unicidade mensal, preservou não-overlap | Manter migration legada; não reabrir neste PR |
| #32 | Backup GDrive | Timeout, sanitização e mídia | Manter |
| #33 | Comandos de backup | Operação read-only/check e backup manual | Manter |
| #34 | Limpeza | Removeu seed legado e artefato `user` | Manter; deploy de banco novo documentará bootstrap explícito |
| #35 | Ambiente rápido PostgreSQL | Criou settings/Compose isolados | Manter; corrigir runner paralelo |
| #36 | Fundação receivables + accounts.0024 | Contrato original: plano elegível **e** flag por tenant | Manter contrato; reconstruir migration inicial de receivables |
| #37 | Provider/emissão idempotente | Separou efeito externo da transação | Manter |
| #38 | Lifecycle e outbox | Adicionou estado financeiro/outbox | Manter, validar schema canônico |
| #39 | Routing de boleto em webhook | Impediu descarte como evento estrangeiro | Manter |
| #40 | PostgreSQL-only | Removeu SQLite como matriz suportada | Manter |
| #41 | Reconciliação de boletos | Task independente tenant-scoped | Manter; flag deve bloquear mutações |
| #42 | Allocation boleto→venda | Endpoint posterior permitiu seller indevidamente | Manter domínio, restringir autorização |
| #43 | Impacto em comissão | Review para períodos congelados | Manter; gestor/financeiro conforme matriz |
| #44 | Customer Ledger | Novo app/projeção | Manter sem expansão; reconstruir migration inicial |
| #45 | API/backfill Customer Ledger | Sem frontend; escopo ADMIN/MANAGER | Manter, sem módulo novo |
| #46 | Documentos fiscais + `receivables.0005` | Migration normal foi publicada no branch ainda inédito | Incorporar campos na nova `0001`; remover `0005` |
| #47 | Backup/restore de mídia | Segurança de restore test-only | Manter |
| #48 | UI/API do piloto | Criou boleto, allocation e review; autorização financeira ampla | Corrigir permissions e contratos de criação |
| #49 | Notificações/ops | Reminder atual termina como sucesso sem envio real | Corrigir estados/entrega idempotente |
| #50 | CI PostgreSQL/Redis | `--parallel` sem `tblib` mascara falhas | Corrigir dependência/estratégia |
| #51 | Bloqueadores de criação | Corrigiu barcode/URL, seller e idempotência | Manter e ampliar testes |
| #52 | Delivery durável | Criou delivery/outbox, mas reminder paralelo não usa entrega real | Manter modelo; corrigir task |
| #53 | Hardening + `customers.0002` | Acrescentou source/constraint em migration separada | Consolidar em nova `customers.0001` |
| #54 | Planos/preços | Fora do PR atual; duplicou matriz de features no dashboard | Não alterar comercial; remover apenas fonte técnica duplicada |
| #55 | Edição/anulação de vendas | Restaura correção auditada | Manter |
| #56 | UI novo boleto | Visual; sem mudança de contrato | Manter; corrigir apenas bugs funcionais |
| #57 | PWA branding | Manifest unificado | Manter |
| #58 | Botões de venda | Visibilidade | Manter |
| #59 | Reescreveu migrations customers e tornou `0002` no-op | Primeira evidência de migration publicada sendo alterada para contornar banco | Substituir cadeia inédita por `0001` canônica |
| #60 | Reescreveu `receivables.0005` com SQL condicional | Introduziu autocura/DDL e estado divergente | Remover integralmente |
| #61 | Reescreveu novamente `0005` via ORM | Corrigiu FK física hardcoded, mas preservou migration mutável | Remover integralmente; usar migration gerada |
| #62 | Central de Cobranças | Façade desktop inicial; preservou rotas antigas | Manter conceito; extrair query service |
| #63 | Restaurou boleto/KPIs/criação | Separou histórico de criação, mas mudança posterior neutralizou flag global | Manter histórico; restaurar política operacional |
| #64 | Evitou descriptografar PII em agregações e completou stats | Correção parcial da Central | Manter princípio; centralizar consulta |
| #65 | Matriz XLSX dinâmica | Funcionalidade já existe; review apontou tipo de data, BytesIO e vazamento de exceção | Não duplicar/expandir; corrigir apenas regressões e documentar lacunas |

## Linha causal crítica

```text
#36 receivables.0001 + flag correta
  -> #46 campos fiscais em 0005
  -> #60 reescreve 0005 com DDL condicional
  -> #61 reescreve 0005 novamente
  -> commits pós-PR criam/expandem 0008 reparadora

#62 Central inicial
  -> #63 restaura histórico/criação/KPIs
  -> #64 evita PII em agregados
  -> commits pós-PR removem gates e quebram testes da flag
```

## PRs regressivos ou parciais

- Regressivos: #59 (história mutável), #60 (DDL condicional), #61 (correção parcial sobre migration já reescrita).
- Parciais: #48 (permissions), #49/#52 (delivery reminder), #62/#63/#64 (Central ainda em views e política de flag fragmentada), #50 (runner paralelo), #65 (review não resolvido e implementação não prevista pelo responsável).
- A regressão que neutraliza `receivables_enabled` ocorreu em commit direto `609b40b`, depois dos PRs da Central, não em um PR próprio.
- A `receivables.0008` surgiu nos commits diretos `cfc90bd` e `2c40bb4`.
