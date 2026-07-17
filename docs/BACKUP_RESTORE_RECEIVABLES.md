# Backup e Restore de Recebíveis (Mídia)

## Visão Geral

O backup do QueroLink cobre dois componentes:

1. **PostgreSQL** — dump completo do banco (`pg_dump -Fc -Z9`)
2. **Media** — todos os arquivos de mídia (`MEDIA_ROOT`), incluindo PDF/XML fiscais de boletos

Ambos são compactados, checksumados e enviados ao Google Drive com um manifesto de verificação.

## Backup

### Execução

O backup é executado automaticamente pela task Celery `daily_backup` (agendada no Celery Beat). Execução manual:

```bash
./scripts/backup.sh
```

### O que é gerado

Para cada execução, três arquivos são criados em `$BACKUP_DIR` e enviados ao Google Drive:

| Arquivo | Conteúdo |
|---|---|
| `querolink_<timestamp>.dump` | Dump PostgreSQL |
| `querolink_<timestamp>.media.tar.gz` | Arquivo compactado de `MEDIA_ROOT` |
| `querolink_<timestamp>.manifest.json` | Manifesto com metadados e SHA-256 |

### Estrutura do manifesto

```json
{
  "timestamp": "2026-07-17_120000",
  "created_at": "2026-07-17T12:00:00Z",
  "database": "dbname@dbhost:5432",
  "files": {
    "dump": {
      "filename": "querolink_2026-07-17_120000.dump",
      "size_bytes": 123456,
      "sha256": "abcdef..."
    },
    "media": {
      "filename": "querolink_2026-07-17_120000.media.tar.gz",
      "size_bytes": 78901,
      "sha256": "123456..."
    }
  }
}
```

### Validação do backup

O script verifica:

1. pg_dump gerou arquivo não vazio
2. `MEDIA_ROOT` existe e foi compactado
3. Checksums SHA-256 foram calculados
4. Manifesto foi criado
5. Todos os 3 arquivos foram enviados ao Google Drive
6. Todos os 3 arquivos existem remotamente

**Qualquer falha em um destes passos marca o backup como falho.** Não há sucesso parcial.

### Retenção

| Local | Período | Padrão |
|---|---|---|
| Local (`BACKUP_DIR`) | `$LOCAL_RETENTION_DAYS` | 2 dias |
| Remoto (Google Drive) | `$REMOTE_RETENTION_DAYS` | 30 dias |

### Variáveis de ambiente

A maioria das variáveis já configuradas no backup legado permanece válida:

| Variável | Default | Descrição |
|---|---|---|
| `DATABASE_URL` | — | URL do banco PostgreSQL (obrigatório) |
| `MEDIA_ROOT` | `/app/media` | Diretório de mídia |
| `GDRIVE_REMOTE` | `gdrive` | Nome do remote rclone |
| `GDRIVE_PATH` | `querolink-backups` | Pasta no Google Drive |
| `PG_DUMP_BIN` | `pg_dump` | Binário do pg_dump (útil em testes) |
| `RCLONE_BIN` | `rclone` | Binário do rclone (útil em testes) |
| `LOCAL_RETENTION_DAYS` | `2` | Retenção local |
| `REMOTE_RETENTION_DAYS` | `30` | Retenção remota |

## Restore em Ambiente de Teste

### Script

```bash
./scripts/restore_to_test.sh [latest|YYYY-MM-DD_HHMMSS]
```

### Pré-requisitos

- Banco PostgreSQL **vazio** ou descartável
- `DATABASE_URL` apontando para o banco de teste
- Acesso ao Google Drive via rclone
- `SAFETY_BLOCKED_HOSTS` configurado em produção para bloquear acidentes

### Safety Gate

O script recusa executar se:

- `$SAFETY_BLOCKED_HOSTS` contém o host do banco (lista separada por vírgula de hosts de produção)
- O usuário não confirma interativamente digitando `SIM`

### Fluxo

1. Localiza o backup mais recente (ou específico) no Google Drive
2. Baixa dump, mídia e manifesto
3. Verifica SHA-256 de ambos os arquivos contra o manifesto
4. Restaura o dump com `pg_restore --clean --if-exists --no-owner --no-acl`
5. Extrai a mídia em `$TEST_MEDIA_DIR`
6. Executa `python manage.py check`

### Variáveis do restore

| Variável | Default | Descrição |
|---|---|---|
| `DATABASE_URL` | — | URL do banco de teste (obrigatório) |
| `SAFETY_BLOCKED_HOSTS` | — | Hosts bloqueados (segurança) |
| `TEST_MEDIA_DIR` | `/tmp/querolink_restore_media` | Diretório de restore da mídia |
| `PROJECT_DIR` | `..` relativo ao script | Diretório do projeto Django |
| `DJANGO_SETTINGS_MODULE` | `app.config.settings.test_fast` | Settings para o `check` |

## Rollback

### Se o restore falhar

1. A transação do `pg_restore` falha automaticamente em erro
2. O diretório `TEST_MEDIA_DIR` pode conter dados parciais — apague manualmente
3. Corrija a causa (checksum divergente, backup corrompido, versão incompatível) e tente novamente

### Se o deploy falhar após restore

1. Desative a feature flag `receivables_enabled` no tenant afetado
2. Preserve os dados financeiros recebidos (nunca apagar pagamentos confirmados)
3. Volte a aplicação para a tag `prod-*` anterior
4. Refaça o backup do estado atual para investigação em banco separado

## Verificação Manual (Pré-merge)

Antes de considerar o backup/restore validado, execute em ambiente descartável:

```bash
# 1. Upload de documento fiscal
# (usar UI ou API para enviar PDF/XML)

# 2. Executar backup
./scripts/backup.sh

# 3. Remover banco e mídia de teste
dropdb -h localhost -U postgres querolink_test
mkdir -p /tmp/media_backup_orig
cp -r app/media/* /tmp/media_backup_orig/
rm -rf app/media/*

# 4. Restaurar
DATABASE_URL=postgres://postgres@localhost/querolink_test \
  ./scripts/restore_to_test.sh latest

# 5. Verificar
diff -r app/media /tmp/media_backup_orig
sha256sum app/media/receivables/* app/media/receivables/*
python manage.py check
```
