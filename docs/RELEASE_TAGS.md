# Releases, tags e rollback

Este documento registra o fluxo de tags do Mérito by Vidalys.

## Objetivo

Tags servem como pontos oficiais de restauração e auditoria.

Uma branch muda com novos commits. Uma tag fica parada exatamente no commit em que foi criada.

## Branches principais

### `main`

Representa o estado funcional preservado/produção quando o time decide congelar uma versão segura.

### `querolink-v2`

É a branch de integração ativa, onde entram PRs validados antes de novos testes/deploys.

## Convenção de tags

### `prod-*`

Use para marcar uma versão considerada estável ou funcional em produção.

Exemplo:

```bash
prod-2026-07-15-pre-periodos-livres
```

### `rc-*`

Use para marcar uma release candidate, ou seja, uma versão candidata para teste/deploy.

Exemplo:

```bash
rc-2026-07-15-periodos-livres
```

## Tags atuais criadas em 15/07/2026

### Produção preservada antes de períodos livres

```bash
prod-2026-07-15-pre-periodos-livres
```

Uso:

```bash
git checkout prod-2026-07-15-pre-periodos-livres
```

Essa tag aponta para a `main` no estado funcional preservado antes do PR de períodos livres.

### Release candidate com períodos livres

```bash
rc-2026-07-15-periodos-livres
```

Uso:

```bash
git checkout rc-2026-07-15-periodos-livres
```

Essa tag aponta para a `querolink-v2` com o PR de períodos livres mergeado.

## Fluxo recomendado

1. Desenvolver e testar em branch pequena.
2. Abrir PR para `querolink-v2`.
3. Validar CI e auditoria.
4. Fazer merge na `querolink-v2`.
5. Se a versão for candidata a deploy, criar tag `rc-*`.
6. Testar/deployar.
7. Se aprovada como produção, preservar com tag `prod-*` ou `v*`.
8. Se der problema, voltar para a última tag `prod-*`.

## Criar tag de produção

```bash
git checkout main
git pull --ff-only origin main
git tag -a prod-AAAA-MM-DD-descricao -m "Descrição da produção estável"
git push origin prod-AAAA-MM-DD-descricao
```

## Criar tag release candidate

```bash
git checkout querolink-v2
git pull --ff-only origin querolink-v2
git tag -a rc-AAAA-MM-DD-descricao -m "Descrição da release candidate"
git push origin rc-AAAA-MM-DD-descricao
```

## Voltar para uma tag localmente

```bash
git checkout nome-da-tag
```

Exemplo:

```bash
git checkout prod-2026-07-15-pre-periodos-livres
```

Isso coloca o repositório em modo detached HEAD. É esperado para inspeção, teste ou preparação de rollback.

Para voltar ao desenvolvimento:

```bash
git checkout querolink-v2
```

## Regra de segurança

Nunca mover, recriar ou apagar uma tag `prod-*` sem autorização explícita.

Se uma tag foi criada errada, criar uma nova tag corrigida com outro nome e documentar o motivo.
