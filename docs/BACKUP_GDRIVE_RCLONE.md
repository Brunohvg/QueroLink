# Backup Google Drive com rclone

Este documento explica como gerar as variáveis do rclone para o `.env` do servidor.

## Regra principal

No Windows PowerShell, se o `rclone.exe` está na pasta atual, use:

```powershell
.\rclone.exe config
```

Não use apenas:

```powershell
rclone config
```

O PowerShell não executa programas da pasta atual sem `.\`.

## Script recomendado

Na pasta onde está o `rclone.exe`, copie/rode o script:

```powershell
.\generate-rclone-gdrive-env.ps1
```

Ou informe caminho customizado:

```powershell
.\generate-rclone-gdrive-env.ps1 -RemoteName gdrive -DrivePath merito-backups
```

O script:

1. detecta o `rclone.exe`;
2. abre o assistente do rclone se o remote `gdrive` não existir;
3. testa acesso ao Google Drive;
4. cria/testa a pasta `merito-backups`;
5. imprime as variáveis corretas para o `.env`;
6. salva uma cópia local em `merito-rclone-env.txt`.

## Variáveis esperadas no servidor

```env
RCLONE_CONFIG_GDRIVE_TYPE=drive
RCLONE_CONFIG_GDRIVE_SCOPE=drive
RCLONE_CONFIG_GDRIVE_TOKEN={"access_token":"...","token_type":"Bearer","refresh_token":"...","expiry":"..."}
GDRIVE_PATH=merito-backups
```

O campo `RCLONE_CONFIG_GDRIVE_TOKEN` deve receber o JSON inteiro, não apenas o `refresh_token`.

## Para o token não expirar rápido

Ao configurar o rclone:

- deixe `client_id` vazio;
- deixe `client_secret` vazio;
- use o navegador para autenticar;
- copie o JSON completo do token;
- não use `...`;
- deixe tudo em uma linha no `.env`.

O `access_token` expira normalmente. O `refresh_token` dentro do JSON é o que permite renovar automaticamente.

## Validar no servidor

Depois de atualizar o `.env`, reinicie o worker e o beat:

```bash
docker compose up -d --force-recreate celery_worker celery_beat
```

Depois teste dentro do container do worker:

```bash
rclone listremotes
rclone about gdrive:
rclone lsd gdrive:
rclone mkdir "gdrive:${GDRIVE_PATH:-merito-backups}"
/app/scripts/backup.sh
rclone lsf "gdrive:${GDRIVE_PATH:-merito-backups}" --include "querolink_*.dump"
```

## Segurança

Nunca commite:

- `merito-rclone-env.txt`;
- `rclone.conf`;
- qualquer token OAuth;
- qualquer refresh token.
