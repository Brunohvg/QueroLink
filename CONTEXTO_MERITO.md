# CONTEXTO_MERITO.md — DOSSIÊ COMPLETO DO SISTEMA PARA IA CONSELHEIRA/AUDITORA
**Atualizado em: 05/07/2026 (v12 do código) · Mantenedor: Bruno Vidal · Atualizar a seção 9 a cada sessão**

> **COMO USAR ESTE DOCUMENTO:** Cole no início de uma conversa com qualquer IA (Claude, GPT, Gemini) quando precisar de análise, auditoria, decisão de produto ou criação de prompts para o Mérito. Ele transfere o contexto, a metodologia e as lições aprendidas de meses de trabalho. Não é para a IA que escreve código (essa segue o `AGENTS.md` na raiz do projeto) — é para a IA que pensa junto.

---

# 1. O NEGÓCIO

**Vidalys** é a holding de SaaS de Bruno Vidal (Minas Gerais) para o varejo brasileiro. **Mérito by Vidalys** (merito.vidalys.com.br) é o produto principal: gestão de comissões e vendas para lojas de varejo com vendedores comissionados, nicho inicial: lojas de artesanato/aviamentos. **Bibelô** (bibelo.com.br) é a loja física/online do próprio Bruno — design partner, primeiro cliente e campo de testes (≈16 vendedores). Bruno é dev experiente (Django) e opera via agente de código IA (OpenCode/DeepSeek) executando prompts numerados que a IA conselheira escreve.

**Preços (fonte da verdade: `PLAN_PRICES` nos settings — nunca hardcode):** Starter R$147/mês (5 vendedores) · Pro R$297 (15) · Business R$497 (50) · anual = mensal × 10. Features gated no Pro+: export contábil, PDF, import CSV, prévia de fechamento, web push. WhatsApp e links de pagamento em TODOS os planos. Calculadora de frete: todos os planos (decisão estratégica: retenção via uso diário do vendedor). ENTERPRISE existe como legado — não ofertar.

**Diferencial competitivo real:** o fluxo contábil de ponta a ponta (fechamento → pacote ZIP com CSVs+PDF+CPFs → e-mail automático ao contador quando tudo fecha). Nenhum concorrente do nicho tem. **Estratégia guardada (Horizonte 3):** portal do contador multi-cliente — contador atende dezenas de lojistas e vira canal de distribuição. Só com 5+ tenants pagantes.

**Personas:** Gestor (desktop, dashboard completo), Vendedor (mobile PWA: lançar venda, minhas vendas, ranking COM PRIVACIDADE — não vê totais dos colegas —, frete, perfil), Contador (sem login; recebe por e-mail), Financeiro (fila de pagamento).

# 2. STACK E ARQUITETURA

Django 5.x · DRF · Celery + Redis · PostgreSQL · Alpine.js (build NORMAL — ver landmine 4.4) · Tailwind (buildado, sem CDN) · PWA · Pagar.me (links de pagamento dos clientes finais) · Mercado Pago (billing das assinaturas) · Evolution API (WhatsApp) · Sentry (PII off) · Deploy: Docker Compose no Coolify, Oracle Cloud ARM64 · Backups: pg_dump → rclone → Google Drive (02:00).

**Multi-tenant por FK:** tudo referencia `Tenant`; middleware injeta `request.user.tenant`. Isolamento por queryset, não por schema. Unicidades são quase sempre POR TENANT.

**Apps principais:** `accounts` (Tenant, User com roles ADMIN/MANAGER/SELLER/FINANCIAL, onboarding, working days) · `sellers` (Seller 1-1 User via `related_name='seller_profile'`, CPF validado/único por tenant) · `sales` (Sale MANUAL|LINK — **só MANUAL entra em comissão**, LINK é informativo) · `commissions` (o coração — ver §3) · `billing` (MP, assinaturas) · `payments` (Pagar.me, webhooks idempotentes HMAC) · `notifications` (WhatsApp retry exponencial + requeue + dedup, push, lifecycle emails, e-mail contábil) · `freight` (calculadora: cascata CWS oficial → tabela estimada) · `webhooks` · `audit` · `api` · `dashboard` (desktop_views gestor + mobile_views vendedor).

# 3. O DOMÍNIO DE COMISSÕES (entender antes de opinar sobre qualquer coisa)

- `CommissionPeriod` (tenant+mês+ano): ABERTA / PARCIALMENTE_FECHADA / FECHADA / PARCIALMENTE_PAGA / PAGA / CANCELADA — o status do período é DERIVADO dos status por vendedor
- `SellerCommission` (por vendedor por período): ABERTA / REABERTA / FECHADA / AJUSTADA / PAGA / CANCELADA — granularidade POR VENDEDOR é sagrada (lojas pagam vendedores em datas diferentes); qualquer proposta de fluxo linear por período DESTRÓI capacidade — já foi proposta e rejeitada
- Fechar congela a taxa (`freeze`), com `select_for_update`; reabrir/ajustar auditado (`CommissionAdjustment`, FK `seller_commission`); pagar registra data/usuário/observação
- **"Enviada à contabilidade" é EVENTO (timestamp `sent_to_accounting_at/by`), não status** — decisão tomada após análise; o envio automático dispara quando TUDO FECHA (não quando paga — folha é processada ANTES do pagamento)
- Auto-sync de vendedores: na LIST do ViewSet (a tela de fechamento só usa list!) + `ensure_seller_commission` ao lançar venda. Sync no retrieve existe mas o front não o chama
- `related_name` críticos: `period.seller_commissions` / `seller.commissions` / `user.seller_profile` / `seller.sales` — usar `sellercommission_set` já causou 500 em produção
- Exclusão de vendedor: proibida com vendas (desativar em vez de excluir); FKs de Sale/SellerCommission→Seller devem ser PROTECT (Prompt 27)

# 4. LANDMINES — OS BUGS QUE SE REPETEM (decorar)

**4.1 · `json.dumps` + `json_script` = tela em branco (5 OCORRÊNCIAS).** A view passa o OBJETO Python; o filtro serializa sozinho. Dumps duplo → JS recebe string → Alpine renderiza nada. Já quebrou: minhas_vendas, links gestor, links mobile, presets do frete, editor de mensagens. TODO parse no template usa o padrão defensivo (`typeof x === 'string' ? JSON.parse(x) : x`). Ao revisar qualquer código novo com JSON→template, grep primeiro.

**4.2 · Migrations:** data migration (normalizar/dedupe) SEMPRE antes da constraint que depende dela — a ordem já foi revertida DUAS vezes por regressão do agente; existe teste anti-regressão (`forwards_plan`) que quebra a suíte se inverterem de novo. O entrypoint roda `migrate --noinput` no boot: migration quebrada = deploy derrubado. NUNCA aceitar constraints não pedidas (uma constraint global de e-mail quase quebrou o cadastro de vendedores — **vendedores são criados SEM e-mail**, `email=''`).

**4.3 · Validação mora no caminho, não no model:** `Seller.clean()` não roda no DRF — serializers precisam de `validate_*` próprio (o CPF duplicado passou por aí). Validador compartilhado em `sellers/validators.py`.

**4.4 · CSP + Alpine:** o Alpine local é o build NORMAL — EXIGE `'unsafe-eval'` no CSP. Removê-lo mata TODO o frontend, e só em produção (dev era Report-Only). Já aconteceu por "melhoria espontânea" do agente.

**4.5 · Celery:** `log_action(request, ...)` NÃO aceita kwarg `tenant` — em task, `AuditLog.objects.create` direto (TypeError após e-mail enviado + retry = contador recebeu e-mails duplicados). `datetime - date` = TypeError (converter com `localtime().date()`). Loop de tenants: try/except POR tenant. Efeito externo + exceção depois + retry = efeito duplicado.

**4.6 · Diversos:** `.filter()` após slice crasha · `seller.commission_rate` pode ser None → sempre `get_commission_rate(seller)` · dinheiro SEMPRE em centavos (int) · input de request valida tipo (string onde se espera int já deu 500) · segredos em `EncryptedCharField`, forms com `••••••••` e POST ignora esse valor · `_check_role` aceita ADMIN **e** MANAGER · nunca `@csrf_exempt` (templates mandam X-CSRFToken) · backup `-Fc` restaura com `pg_restore` direto (NÃO é gzip).

# 5. METODOLOGIA DE AUDITORIA (a cada versão nova — via GitHub ou ZIP)

**Fonte preferencial: o repositório GitHub** (branch `querolink-v2` ou a branch do prompt). Se a IA tem acesso ao GitHub (conector do GPT, MCP, etc.), auditar direto do repo: comparar o commit/branch novo contra o anterior (`git diff`, compare view, ou lista de commits desde a última auditoria). ZIP é o fallback quando não houver acesso ao repo — o método é o mesmo, muda só a origem do diff.

1. **Diff contra a versão anterior** (no repo: diff entre commits/branches; em ZIP: `diff -rq --exclude=__pycache__`) — a lista de arquivos alterados deve bater 1:1 com o prompt executado. Arquivo extra = investigar IMEDIATAMENTE (as duas piores quebras vieram de mudanças não pedidas). Com GitHub, exigir do agente **um commit (ou branch) por prompt** com o número no nome (`prompt-27-exclusao-segura`) — isso torna o diff auditável por construção e elimina a classe de regressão "partiu de base velha"
2. **Verificar cada item do prompt no código** — não confiar no relatório do agente; o drift clássico é cumprir a letra e errar o lugar (ex.: sync no retrieve quando a tela usa list)
3. **Provar bugs por simulação quando possível** (rodar o Python/bash que demonstra) — afirmação sem prova não entra no relatório
4. **Varreduras fixas:** grep de `json.dumps` em dashboard/*.py · `sellercommission_set` · `csrf_exempt` · `float(seller.commission_rate)` · ordem de migrations com `migrate --plan` · sintaxe global com ast.parse
5. **Cada bug achado → prompt de correção**, nunca instrução solta (exceto correções de 2 linhas, que podem ir inline)
6. **Regressões acontecem** — o agente já reverteu correções validadas partindo de base velha (2x). Com o repo: antes de aprovar qualquer entrega, verificar que a branch partiu do HEAD atual (nenhum commit anterior "sumiu" do diff) e que as correções das landmines seguem presentes (greps do item 4). CI rodando `manage.py test` + `check --deploy` a cada push é a recomendação nº 1 permanente — se ainda não existir, propor o workflow do GitHub Actions.

# 6. FORMATO DOS PROMPTS PARA O OPENCODE

Numerados (PROMPT_N), abrindo com **"Siga o AGENTS.md"** (as regras permanentes moram lá — não repetir). Estrutura: contexto com a CAUSA confirmada → lotes numerados → cada lote termina com gate **"PARE AQUI"** exigindo evidência COLADA (saída de grep/teste/migrate --plan) → QA final com lista fechada de arquivos permitidos → tabela-resumo. Decisões já tomadas vão como fato ("decisão: X — não reavaliar"), nunca como pergunta aberta, senão o agente improvisa. Escopo cirúrgico sempre: prompts amplos ("melhore a tela") geram destruição.

# 7. OPERAÇÃO

- **Deploy:** Coolify, compose com web + redis + celery worker + beat (healthchecks em tudo). Entrypoint valida SECRET_KEY/DATABASE_URL/FERNET_KEY, espera DB, migra, collectstatic, gunicorn. Staging = merito.vidalys.com.br
- **Beat:** backup 02:00 · lembretes de lançamento a cada 15min (respeitam dias de funcionamento/feriados do tenant) · reconcile Pagar.me 30min · requeue notificações 10min · lifecycle emails 09:00 · cleanup webhooks diário
- **Backup:** pg_dump -Fc → rclone → Google Drive (remote `gdrive`, env vars `RCLONE_CONFIG_GDRIVE_*` no worker). Restauração: `pg_restore` direto no `.dump`. Testar restauração num banco vazio antes de confiar
- **E-mails:** reset de senha tem template HTML profissional (tables, CSS inline, identidade); os demais (contábil, lifecycle, credenciais) ainda são texto puro — unificação é P1 do roadmap
- **Identidade visual:** graphite `#0B1120` · Electric Blue `#1263FF` · Cyan `#00D1E6` · surface `#F5F7FA` · Inter (UI) + Space Grotesk (display) · lockup "Mérito by Vidalys" · fontes locais, zero CDN

# 8. DECISÕES DE PRODUTO JÁ TOMADAS (não reabrir sem fato novo)

- Contador NÃO tem login (fase atual): recebe por e-mail. Portal = Horizonte 3
- Envio contábil dispara no FECHAMENTO total, não no pagamento; reenvio manual nunca é bloqueado; dedup só no automático e por competência
- CPF: opcional, dígitos normalizados, único por tenant, vendedor cadastra o próprio SÓ quando vazio (alterar = gestor); pendência AVISA, nunca bloqueia o pacote
- Vendedor com vendas: desativa, não exclui. Excluir limpo remove o User junto (sem órfãos)
- Ranking mobile preserva privacidade (sem totais dos colegas)
- Dias de funcionamento por tenant (default Seg–Sáb) + feriados nacionais (lib `holidays`): suprime cobranças, NUNCA altera cálculo de comissão
- Frete: cascata credencial CWS → tabela estimada (calibrável ±% pelo gestor, que NÃO se aplica ao valor oficial); rótulo "estimativa" obrigatório no modo tabela; sem feature gate
- NÃO fazer: app nativo, mais gateways, CRM embutido, feature pedida por 1 cliente só

# 9. ESTADO ATUAL E PENDÊNCIAS ⚠️ (ATUALIZAR A CADA SESSÃO)

**Em 05/07/2026, v12:** ciclo completo validado em staging COM e-mail real chegando ao contador (criar → vender mobile → fechar → contabilidade → pagar → histórico, zero 500s). Prompts 1–26 executados e verificados.

**Pendentes de execução:** PROMPT_27 (exclusão segura de vendedor: perform_destroy deletando User junto, PROTECT nos FKs, `fix_orphan_users`, bloqueio de exclusão com vendas) · PROMPT_28 (backup: parse DATABASE_URL quebrado — provado por simulação que só popula DB_HOST —, pipefail, rclone multi-arch, upload verificado com copyto+lsf, task com raise, extensão `.dump`).

**Pendentes operacionais:** aplicar 27+28 → verificar ZIP → gerar token rclone → teste manual do backup no worker → teste de RESTAURAÇÃO em banco vazio → go-live da Bibelô. Users órfãos no staging (limpar com `fix_orphan_users --apply`). Verificar se o console parou de acusar CSP/eval após o último deploy (se persistir: proxy do Coolify injetando header).

**Roadmap pós-launch:** Git+CI (prioridade nº 1 — regressões só serão estruturalmente resolvidas com isso) · unificar e-mails no template do reset · resumo executivo mensal do gestor · metas por vendedor · declaração de conteúdo Correios em PDF · calibrar tabela de frete no balcão real · Nuvemshop (mês 3+) · revisar preço do Pro (R$397–497) com 3 clientes externos validados.

# 10. CHECKLIST RÁPIDO A CADA ENTREGA (branch/commit no GitHub, ou ZIP)

☐ diff contra a versão anterior (repo: compare de commits/branches; ZIP: diff -rq) — arquivos batem com o prompt? ☐ a branch partiu do HEAD atual? (nada validado antes pode ter sumido) ☐ algo NÃO pedido mudou? (investigar primeiro) ☐ greps das landmines (4.1, 4.6) ☐ migrations: ordem + `--plan` ☐ sintaxe global ☐ itens do prompt verificados UM A UM no código ☐ testes novos são reais (request de verdade, não mock da view)? ☐ evidências dos gates presentes no relatório do agente? ☐ veredito honesto: o que passou, o que falhou, o que ficou fora — e o próximo prompt se necessário.
