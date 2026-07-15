# AGENTS.md — REGRAS OBRIGATÓRIAS DO PROJETO MÉRITO BY VIDALYS

Este arquivo é lido automaticamente em toda sessão. As regras abaixo são INEGOCIÁVEIS e existem porque cada uma corresponde a um bug real que quebrou (ou quase quebrou) produção. Violar qualquer uma invalida a entrega.

## 1. ESCOPO — A REGRA MAIS IMPORTANTE

- Execute EXATAMENTE o que o prompt pede. NADA além.
- **PROIBIDO** fazer melhorias espontâneas de qualquer tipo: segurança, performance, refactors, constraints, "aproveitando que estou aqui". Duas melhorias espontâneas já quebraram produção neste projeto (constraint de e-mail que travava o cadastro de vendedores; alteração de CSP que matava todo o frontend).
- Se identificar um problema fora do escopo: REPORTE no relatório final como sugestão. NÃO implemente.
- Se algo do prompt for impossível ou parecer errado: PARE e explique. NÃO substitua por uma "abordagem melhor" por conta própria.
- O diff da entrega deve conter SOMENTE os arquivos listados no prompt. Qualquer arquivo extra alterado = entrega rejeitada.

## 2. TEMPLATES + JSON (bug reincidente — 4 ocorrências)

- Ao passar dados para `{{ var|json_script:"id" }}`, a view passa o **objeto Python** (list/dict). **NUNCA `json.dumps()`** — o filtro serializa sozinho; dumps duplo entrega uma STRING ao JavaScript e a tela renderiza vazia.
- Todo `JSON.parse` de `json_script` no template usa o parse defensivo:
  ```javascript
  var _p = JSON.parse(document.getElementById('ID').textContent || '[]');
  if (typeof _p === 'string') _p = JSON.parse(_p);
  if (!Array.isArray(_p)) _p = [];
  ```
- Antes de entregar, rode e cole no relatório:
  `grep -rn "json.dumps\|_json.dumps" app/apps/dashboard/*.py | grep -v test`
  Nenhuma variável criada com dumps pode ser consumida por json_script.

## 3. MIGRATIONS (2 incidentes: ordem revertida e constraint destrutiva)

- **NUNCA** crie constraints, índices ou migrations que não foram explicitamente pedidos.
- Data migrations que preparam dados (normalizar, deduplicar) rodam **ANTES** da constraint que dependem delas. Confirme com `python manage.py migrate --plan` e cole a saída.
- NUNCA reordene dependencies de migrations existentes sem instrução explícita.
- Lembre: o entrypoint roda `migrate --noinput` no boot — uma migration que falha DERRUBA O DEPLOY.
- Contexto multi-tenant: unicidade quase sempre é POR TENANT (`fields=['tenant', 'campo']`), nunca global. Vendedores são criados SEM e-mail (`email=''`) — qualquer unicidade em e-mail quebra o sistema.

## 4. FRONTEND / CSP / ALPINE

- O Alpine.js local (`static/js/alpine.min.js`) é o build NORMAL: **exige `'unsafe-eval'` no CSP**. NUNCA remova `unsafe-eval` do `csp_middleware.py` — isso mata todas as telas do sistema, e só em produção (invisível em dev).
- Zero CDNs novos. Fontes e libs são servidas de `static/`.
- Toda view de gestor usa o padrão `_check_role(request, User.Role.ADMIN, User.Role.MANAGER)` — MANAGER e ADMIN, nunca só ADMIN.
- Nunca use `@csrf_exempt` em views de sessão — os templates já enviam `X-CSRFToken` via meta tag.

## 5. QUERIES E DADOS

- NUNCA `.filter()` depois de slice (`[:N]`) — crasha. Filtre primeiro, fatie por último.
- `seller.commission_rate` pode ser `None` (herda a default do tenant). Use `get_commission_rate(seller)` — nunca `float(seller.commission_rate)` direto.
- Valores monetários são SEMPRE em centavos (int). Formatação BR só na exibição.
- Input do usuário em endpoints: sempre validar tipo (int/clamp) antes de usar — request body pode trazer string onde se espera número.

## 6. TASKS CELERY

- `log_action(request, action, instance, changes)` — a assinatura NÃO tem kwarg `tenant`. Em tasks (sem request), crie `AuditLog.objects.create(...)` direto.
- Exceção DEPOIS de um efeito externo (e-mail enviado, API chamada) + retry do Celery = efeito DUPLICADO. Registre o sucesso antes de qualquer operação que possa falhar, ou torne a task idempotente.
- `DateTimeField - date` = TypeError. Converta: `timezone.localtime(dt).date()`.
- Loops sobre tenants em tasks: try/except POR TENANT — um tenant com dado ruim não pode matar o batch.

## 7. SEGREDOS

- Campos sensíveis do tenant usam `EncryptedCharField`.
- Formulários de segredo: value `••••••••` quando salvo; no POST, ignorar se o valor recebido for `••••••••` (padrão existente em configurações — siga-o).
- NUNCA logue tokens, senhas, códigos de acesso ou chaves — nem em warning de erro.

## 8. ENTREGA — GATES OBRIGATÓRIOS

Toda entrega termina com (saídas coladas no relatório):
1. `python manage.py check` limpo
2. `python manage.py test` — suíte COMPLETA passando (não só os testes novos)
3. `python manage.py migrate --plan` se qualquer migration foi criada
4. O grep da regra 2 se qualquer template/view com JSON foi tocado
5. Lista exata de arquivos alterados — que deve bater 1:1 com o escopo do prompt

Relatório final sempre no formato: problema → causa → arquivos alterados → migrations → evidências dos gates.

## 9. RELEASES, TAGS E ROLLBACK

- `main` preserva o estado funcional/produção quando explicitamente solicitado.
- `querolink-v2` é a branch de integração ativa.
- Antes de mudanças estruturais ou de maior risco, preservar o último estado funcional com uma tag `prod-*`.
- Versões novas para teste/deploy devem ser marcadas com tag `rc-*`.
- Tags são pontos fixos: branch anda, tag não anda.
- Nunca mover, recriar ou apagar tag `prod-*` sem autorização explícita.
- O fluxo operacional completo está em `docs/RELEASE_TAGS.md`.

## 10. QUANDO EM DÚVIDA

Pare e pergunte. Uma pergunta custa um turno; uma "decisão criativa" já custou deploys inteiros neste projeto.
