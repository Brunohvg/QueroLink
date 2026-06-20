# Relatório de Prontidão para Produção — QueroLink MVP

> Data: 2026-06-20 | Versão: 1.0.0-MVP

---

## Recomendação

**✅ PRONTO PARA STAGING.**

O sistema está funcionalmente completo, com 55 testes automatizados passando e validação local de ponta a ponta bem-sucedida. O deploy em staging é o próximo passo obrigatório antes do go-live em produção.

O go-live em PRODUÇÃO depende de:
1. Validação completa do checklist em staging (WhatsApp real, PWA real, PostgreSQL real)
2. Backup do banco de produção atual antes de aplicar as novas migrations

---

## Resumo do MVP

| Componente | Quantidade |
|---|---|
| Apps Django | 10 implementados (accounts, sellers, orders, payments, sales, commissions, api, notifications, audit, dashboard) |
| Migrations | 12 (projeto) + 24 (Django/third-party) = 36 total |
| Telas mobile | 5 (login, home, lançar venda, minhas vendas, meu desempenho) |
| Telas desktop | 5 (ranking, vendedores, fechamento, fila aprovação, histórico pagamentos) |
| Telas públicas existentes | 2 (link público, login dashboard) |
| Componentes reutilizáveis | 5 (button, metric_card, status_badge, bottom_nav, sidebar) |
| Endpoints API | 18 (auth + CRUD + ranking + commissions + csv) |
| Permission classes | 3 (IsManagerOrAdmin, IsFinancialOrAdmin, IsSellerOwner) |
| Testes automatizados | 55 (10 sellers + 14 notifications + 6 dashboard + 25 api) |
| PWA | manifest.json (8 ícones), sw.js (cache + offline) |
| Integrações | Pagar.me (webhook existente), Evolution API (WhatsApp) |
| Vendedores seed | 16 (Bibelô) |

---

## O que foi validado (local)

| Item | Resultado |
|---|---|
| Migrations do zero | ✅ Todas aplicam em ordem |
| Seed 16 vendedores | ✅ Users + Sellers criados |
| Centavos sem arredondamento | ✅ R$ 47,90 → 4790 → R$ 47.90 |
| Cálculo de comissão | ✅ `recalculate()` == soma manual |
| Máquina de estados | ✅ ABERTA → EM_CONFERENCIA → ENVIADA → APROVADA → PAGA |
| Isolamento multi-tenant | ✅ 5 testes API passam |
| Swagger API | ✅ `/api/schema/` → 200 |
| Testes automatizados | ✅ 55/55 |

---

## O que NÃO foi validado (requer staging)

| Item | Risco |
|---|---|
| WhatsApp real (Evolution API) | 🟡 Médio — API já funciona no sistema antigo; credenciais de staging precisam ser separadas |
| PostgreSQL real vs SQLite | 🟡 Médio — diferenças de constraint, tipo de campo, performance |
| PWA em celular real | 🟢 Baixo — manifest e sw.js são padrão, testáveis via Chrome DevTools |
| Rate limiting em produção | 🟢 Baixo — testado localmente com `LocMemCache` |
| Celery worker/beat em produção | 🟡 Médio — Redis já configurado no docker-compose, mas fila precisa ser monitorada |

---

## Riscos identificados para produção

1. **Banco compartilhado com sistema antigo**: O `docker-compose.yml` atual aponta para a mesma `DATABASE_URL` do sistema de link de pagamento. As novas migrations vão adicionar tabelas (accounts, sellers, sales, commissions, notifications, audit) que NÃO conflitam com as tabelas existentes (orders, payments). Mas o deploy precisa de backup prévio.

2. **Seed com dados reais**: Os 16 vendedores do `seed.py` são dados reais da loja Bibelô. Em staging, OK. Em produção, os vendedores JÁ existem — o seed não deve ser rodado. A data migration `0003_link_sellers_to_users` foi projetada para lidar com isso (cria Users para Sellers existentes sem User).

3. **WhatsApp em produção**: As credenciais da Evolution API no `.env` de produção são as reais. Qualquer disparo acidental durante deploy/migração pode enviar mensagens para vendedores reais. O fluxo de notificação depende de ações explícitas (criar vendedor, marcar como pago) — não vai disparar sozinho durante o migrate.

4. **Redis compartilhado**: O `docker-compose.yml` compartilha o Redis entre o sistema antigo e o novo. O Celery usa o mesmo Redis como broker. Se o Redis já estiver em uso pesado pelo sistema antigo, pode haver contenção. Monitorar.

---

## Passos para staging

1. Criar app separado no Coolify com o mesmo `docker-compose.yml`
2. Apontar `DATABASE_URL` para um PostgreSQL staging vazio
3. Usar credenciais de WhatsApp de teste (ou confirmar com usuário que pode usar as reais em staging)
4. Rodar `docker-compose up -d --build`
5. Conferir logs do `web` container: migrations devem aplicar sem erro
6. Rodar seed: `docker exec <container> python manage.py shell < seed.py`
7. Executar checklist `VALIDATION_CHECKLIST_LOTE5.md`
8. Se todos os itens passarem → **PRONTO PARA PRODUÇÃO**

## Passos para produção (após staging aprovado)

1. **BACKUP do banco de produção** (pg_dump)
2. Atualizar imagem Docker no app de produção
3. Monitorar logs do migrate: data migration deve criar Users para os Sellers existentes
4. Verificar que o sistema antigo (link público) continua funcionando
5. Executar smoke test: login, lançar venda, fechar mês
