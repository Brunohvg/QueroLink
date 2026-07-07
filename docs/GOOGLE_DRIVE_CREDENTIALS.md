# Guia: Configurar Credenciais do Google Drive para Backup

> **Objetivo:** Conectar o rclone ao Google Drive para backup automático diário do PostgreSQL.
> **Tempo estimado:** 3 minutos
> **Custo:** R$ 0 (15 GB grátis do Google Drive)
> **Setup:** Executar 1 vez. Depois é automático para sempre.

---

## Pré-requisitos

- **Conta Google** (gmail.com ou Google Workspace)
- **Acesso ao servidor** onde o QueroLink está rodando
- **Docker** já rodando com o container `web`
- **Navegador** no seu PC/celular (para autenticar no Google)

---

## Passo a passo

### 1. Acessar o container web

```bash
docker exec -it <nome-do-container-web> bash
```

Exemplo:
```bash
docker exec -it querolink-web bash
```

### 2. Iniciar o setup do rclone

```bash
./scripts/setup-rclone.sh
```

O script vai exibir um resumo das instruções e pausar. Pressione ENTER.

### 3. Configurar o remote `gdrive`

O `rclone config` vai abrir um assistente interativo.

Responda conforme abaixo:

| Pergunta | Resposta |
|----------|----------|
| `name>` | `gdrive` |
| `Storage>` | `18` (Google Drive) |
| `client_id>` | **ENTER** (usa o padrão do rclone) |
| `client_secret>` | **ENTER** (usa o padrão do rclone) |
| `scope>` | `1` (acesso completo ao Drive) |
| `root_folder_id>` | **ENTER** |
| `service_account_file>` | **ENTER** |
| `Edit advanced config?` | `n` |
| `Use web browser to authenticate?` | `n` (não tem navegador no container) |

### 4. Autenticar no Google

Após responder `n` para o navegador, o rclone vai exibir uma **URL longa**:

```
https://accounts.google.com/o/oauth2/auth?client_id=...
```

1. **Copie essa URL inteira**
2. **Abra no navegador do seu PC** (ou celular)
3. **Faça login na sua conta Google**
4. O Google vai perguntar: *"rclone quer acessar sua conta Google"*
5. Clique em **"Continuar"** ou **"Permitir"**
6. O Google vai exibir um **código de verificação** (token longo)
7. **Copie esse código**

### 5. Colar o código de volta no terminal

Volte ao terminal do container e cole o código quando solicitado:

```
Enter verification code> cole_aqui_o_codigo
```

### 6. Finalizar a configuração

Responda as últimas perguntas:

| Pergunta | Resposta |
|----------|----------|
| `Configure this as a Shared Drive?` | `n` (a menos que use Google Workspace com Shared Drives) |
| `y) Yes this is OK` | `y` |
| `e) Edit existing remote` | — (não precisa) |
| `d) Delete remote` | — (não precisa) |
| `q) Quit config` | `q` |

### 7. Verificar que funcionou

O script automaticamente vai:
- Verificar conexão: `rclone ls gdrive:` (lista arquivos do Drive)
- Criar pasta de backups: `rclone mkdir gdrive:querolink-backups`

Se aparecer a listagem (mesmo vazia), **está funcionando**.

### 8. Testar o backup manualmente

```bash
./scripts/backup.sh
```

Se tudo der certo, você verá:

```
[02:00:01] ===== QUEROLINK BACKUP =====
[02:00:02] Executando pg_dump de postgres@host:5432 ...
[02:00:02] Dump criado: /app/backups/querolink_2026-06-29.dump.gz (1.2M)
[02:00:05] Enviando para Google Drive (gdrive:querolink-backups) ...
[02:00:07] Limpando backups remotos > 30 dias ...
[02:00:07] ===== BACKUP CONCLUIDO =====
```

### 9. Confirmar no Google Drive

1. Acesse [drive.google.com](https://drive.google.com)
2. No menu lateral, clique em **"Meu Drive"**
3. Você deve ver a pasta **`querolink-backups`**
4. Dentro dela, o arquivo `querolink_2026-06-29.dump.gz`

---

## O que fazer se algo der errado?

### Erro: "Failed to open: googleapi: Error 403"

**Causa:** O Google bloqueou o acesso porque o app não é verificado.

**Solução:**
1. Vá para [https://myaccount.google.com/security](https://myaccount.google.com/security)
2. Role até "Acesso a apps menos seguros"
3. Ative "Permitir apps menos seguros" (ou similar)
4. Repita o `rclone config` e reautorize

### Erro: "Token expired"

**Causa:** O token OAuth tem validade. O rclone usa refresh tokens, mas se ficar muito tempo sem uso precisa reautorizar.

**Solução:**
```bash
rclone config reconnect gdrive:
# Siga o mesmo fluxo de autenticação (passos 4-6)
```

---

## Segurança

| O que | Onde fica | Risco |
|-------|-----------|-------|
| Token OAuth | `/root/.config/rclone/rclone.conf` (dentro do container) | Baixo — acesso requer acesso root ao container |
| Dump do banco | `/app/backups/*.dump.gz` (dentro do container) | Médio — contém dados criptografados no banco |
| Arquivo no Drive | `querolink-backups/` (Google Drive) | Médio — protegido pela senha da conta Google |

**Recomendações:**
- Use **autenticação de 2 fatores** na conta Google
- O `.env` NUNCA deve conter tokens do Google (não precisa — fica no rclone.conf dentro do container)
- O arquivo `rclone.conf` não está no repositório git (`.gitignore` cobre a pasta de config)
- Faça logout de sessões não reconhecidas em [myaccount.google.com/security](https://myaccount.google.com/security)

---

## Avançado: Usar seu próprio Google Cloud OAuth Client

Para evitar o aviso "app não verificado" e ter mais controle:

1. Acesse [console.cloud.google.com](https://console.cloud.google.com)
2. Crie um projeto → APIs & Services → Credentials
3. Crie um "OAuth 2.0 Client ID" tipo "Desktop App"
4. Copie `Client ID` e `Client Secret`
5. No `rclone config`, informe esses valores em vez de ENTER

```bash
client_id> 123456789-xxxxx.apps.googleusercontent.com
client_secret> GOCSPX-xxxxx
```

Isso é opcional — o padrão do rclone funciona perfeitamente.

---

## Referências

- [Documentação oficial do rclone — Google Drive](https://rclone.org/drive/)
- [rclone config walkthrough](https://rclone.org/commands/rclone_config/)
- [Google Drive API quotas](https://developers.google.com/drive/api/guides/limits) (750 GB/dia upload grátis)
