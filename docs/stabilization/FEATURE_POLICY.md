# Política de feature de recebíveis

## Fontes de verdade

- entitlement por plano: `settings.PLAN_FEATURES`;
- liberação operacional por tenant: `Tenant.receivables_enabled`;
- papel: `User.Role`;
- provider: propriedades de configuração do próprio `Tenant` e provider factory.

`tenant_has_feature(tenant, 'boletos')` exige simultaneamente entitlement do plano e `receivables_enabled=True`.

## Políticas separadas

- consulta de histórico: depende de autenticação, mesmo tenant e escopo do papel; desligar emissão não apaga o histórico;
- nova emissão: exige papel ADMIN/MANAGER/SELLER, entitlement e flag ativa;
- operação financeira: exige ADMIN/MANAGER/FINANCEIRO, entitlement e flag ativa;
- tasks operacionais: iteram apenas tenants habilitados e repetem a policy antes de agir.

A Central usa a política histórica e mantém boletos já existentes visíveis quando a emissão é desligada. As telas/endpoints operacionais antigos continuam bloqueados pelo flag.

Textos comerciais de apresentação foram movidos para `accounts.plans`; o dashboard deixou de declarar uma segunda matriz local.
