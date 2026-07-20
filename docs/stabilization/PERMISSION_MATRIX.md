# Matriz de permissões

| Operação | ADMIN | MANAGER | FINANCEIRO | SELLER |
|---|---:|---:|---:|---:|
| Ver histórico do tenant | sim | sim | sim | somente próprio |
| Criar link | sim | sim | não | próprio |
| Emitir boleto com entitlement + flag | sim | sim | não | próprio |
| Selecionar vendedor na emissão | sim | sim | não | não |
| Cancelar boleto gerencial | sim | sim | não | não |
| Consultar indicadores gerenciais | sim | sim | sim quando endpoint financeiro | não |
| Criar/listar allocation | sim | sim | sim | não |
| Aprovar impacto em comissão | sim | sim | não | não |
| Configuração e operações WhatsApp do tenant | sim | sim | não | não |

Todos os objetos são filtrados por tenant. SELLER deriva o vendedor de `user.seller_profile`; `seller_uuid` enviado por SELLER é rejeitado e não constitui autoridade.

As funções canônicas são `can_view_receivables_history`, `can_create_receivable` e `can_manage_receivable`. O enum real mantido é `FINANCEIRO`.
