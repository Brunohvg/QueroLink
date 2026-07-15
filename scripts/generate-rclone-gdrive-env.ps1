param(
    [string]$RemoteName = "gdrive",
    [string]$DrivePath = "merito-backups"
)

$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Find-Rclone {
    $localExe = Join-Path (Get-Location) "rclone.exe"
    if (Test-Path $localExe) {
        return $localExe
    }

    $cmd = Get-Command "rclone" -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }

    throw "rclone.exe nao encontrado. Entre na pasta onde esta o rclone.exe ou instale o rclone no PATH."
}

function Get-RcloneConfigDump {
    param([string]$Rclone)

    $json = & $Rclone config dump
    if (-not $json) {
        throw "Nao foi possivel ler a config do rclone."
    }
    return ($json | ConvertFrom-Json)
}

function Get-RemoteToken {
    param(
        [object]$Dump,
        [string]$Remote
    )

    $remoteProp = $Dump.PSObject.Properties[$Remote]
    if (-not $remoteProp) {
        return $null
    }

    $tokenProp = $remoteProp.Value.PSObject.Properties["token"]
    if (-not $tokenProp) {
        return $null
    }

    return [string]$tokenProp.Value
}

$rclone = Find-Rclone

Write-Step "Usando rclone em: $rclone"
& $rclone version

Write-Step "Verificando remote '$RemoteName'"
$remotes = & $rclone listremotes
$remoteExists = $remotes -contains "$RemoteName`:"

if (-not $remoteExists) {
    Write-Host ""
    Write-Host "O remote '$RemoteName' ainda nao existe." -ForegroundColor Yellow
    Write-Host "Vou abrir o assistente do rclone agora." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Responda assim:" -ForegroundColor Yellow
    Write-Host "  n) New remote"
    Write-Host "  name> $RemoteName"
    Write-Host "  Storage> drive"
    Write-Host "  client_id> deixe vazio e aperte Enter"
    Write-Host "  client_secret> deixe vazio e aperte Enter"
    Write-Host "  scope> drive ou 1"
    Write-Host "  root_folder_id> deixe vazio"
    Write-Host "  service_account_file> deixe vazio"
    Write-Host "  Edit advanced config? n"
    Write-Host "  Use web browser? y"
    Write-Host "  Shared Drive? n"
    Write-Host "  Keep remote? y"
    Write-Host "  Quit? q"
    Write-Host ""

    & $rclone config

    $remotes = & $rclone listremotes
    $remoteExists = $remotes -contains "$RemoteName`:"
    if (-not $remoteExists) {
        throw "Remote '$RemoteName' nao foi criado. Rode novamente e confira as respostas."
    }
}

Write-Step "Testando acesso ao Google Drive"
& $rclone about "$RemoteName`:"
& $rclone mkdir "$RemoteName`:$DrivePath"
& $rclone lsd "$RemoteName`:"

Write-Step "Extraindo token para o .env do servidor"
$dump = Get-RcloneConfigDump -Rclone $rclone
$token = Get-RemoteToken -Dump $dump -Remote $RemoteName

if (-not $token) {
    Write-Host ""
    Write-Host "Nao encontrei o campo 'token' no remote '$RemoteName'." -ForegroundColor Red
    Write-Host "Confira com:" -ForegroundColor Yellow
    Write-Host "  .\rclone.exe config show $RemoteName"
    throw "Token nao encontrado."
}

if ($token -notmatch '"refresh_token"\s*:') {
    Write-Host ""
    Write-Host "ATENCAO: o token encontrado nao contem refresh_token." -ForegroundColor Red
    Write-Host "Esse token pode expirar rapido ou nao renovar automaticamente." -ForegroundColor Red
    Write-Host "Recrie o remote usando navegador e deixando client_id/client_secret vazios." -ForegroundColor Yellow
}

$envBlock = @"
RCLONE_CONFIG_GDRIVE_TYPE=drive
RCLONE_CONFIG_GDRIVE_SCOPE=drive
RCLONE_CONFIG_GDRIVE_TOKEN=$token
GDRIVE_PATH=$DrivePath
"@

Write-Host ""
Write-Host "COPIE E COLE NO .env DO SERVIDOR:" -ForegroundColor Green
Write-Host "--------------------------------" -ForegroundColor Green
Write-Host $envBlock
Write-Host "--------------------------------" -ForegroundColor Green

$outFile = Join-Path (Get-Location) "merito-rclone-env.txt"
$envBlock | Set-Content -Path $outFile -Encoding UTF8

Write-Host ""
Write-Host "Tambem salvei em: $outFile" -ForegroundColor Green
Write-Host ""
Write-Host "IMPORTANTE:" -ForegroundColor Yellow
Write-Host "- Nao envie esse arquivo para ninguem."
Write-Host "- Nao commite esse arquivo."
Write-Host "- Depois de colar no servidor, reinicie celery_worker e celery_beat."
