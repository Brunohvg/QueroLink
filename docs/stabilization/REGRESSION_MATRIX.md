# Matriz de regressões

| Contrato correto anterior | Mudança | Tentativa posterior | Estado inicial | Estado estabilizado |
|---|---|---|---|---|
| Plano elegível + `receivables_enabled=True` para operar | commit `609b40b` remove gate da policy | guards locais na Central | 5 testes falham; API cria/lista com flag off | gate restaurado e policies view/create/manage separadas |
| Migration imutável e determinística | #60/#61 reescrevem `0005`; commits criam `0008` | autocura genérica | schema depende do histórico | `receivables.0001` canônica validada em PostgreSQL novo |
| Customer app inédito com cadeia linear | #53 cria 0002; #59 move operações para 0001 e deixa marker | no-op 0002 | dívida sem necessidade para banco novo | `customers.0001` canônica, marker removido |
| SELLER só opera seus recebíveis, não financeiro | #48 expõe POST allocation com `IsAuthenticated` | nenhum | seller pode alocar boleto próprio | GET/POST financeiros negados e cobertos por teste |
| Delivery `SENT` só após efeito confirmado | #49 cria reminder; #52 cria delivery durável | task diária cria registro SENT sem enviar | sucesso fantasma | PENDING→SENDING→SENT/FAILED e retry idempotente |
| ADMIN e MANAGER seguem política global | views antigas permanecem ADMIN-only | nenhuma auditoria completa | inconsistência de gestor | revisar cada view e cobrir matriz |
| Uma fonte de verdade de features | #54 adiciona matriz de apresentação no dashboard | settings/model também definem | divergência | usar policy canônica; sem alterar preços |
| Central é façade, aggregates distintos | #62–#64 compõem tudo em `desktop_views.py` | múltiplos hotfixes diretos | funcional porém frágil/duplicado | query service tenant/seller-scoped, DTO estável e testes |
| Seller de boleto deriva da autenticação | #48 tinha envio ambíguo | #51 corrigiu partes | serviço/serializer dividem autoridade | testes negativos de `seller_uuid` e cross-tenant |
| URL/barcode reais do provider | API usava provider order ID | #51 adicionou campos | contrato atual preservado | manter e testar Central/detalhe |
| Runner exibe falhas reais | #50 ativa `--parallel` | nenhum | aborta sem `tblib` | dependência de teste ou CI serial temporário |
| Mensagens não expõem HTML/detalhes | bases usam `innerHTML`; #65 expõe exceção | nenhum | superfície XSS/info leak | render seguro e mensagem genérica |
| CSV não executa fórmulas | exports escrevem texto cru | nenhum | CSV injection | helper de neutralização em todos exports |
| `json_script` parse defensivo | novos templates copiam parsers incompletos | correções pontuais | oito consumidores divergentes | helper/padrão único e testes |
| Importação existente não deve ser duplicada | #65 implementou matriz XLSX | commits `dc8d886`/`a241fd6` corrigem detecção | implementação real e parcial | preservar, testar regressão, documentar lacunas |
