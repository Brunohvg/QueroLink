import os
import re
import subprocess
import tempfile
from pathlib import Path


SECRET_PATTERNS = (
    re.compile(r'ya29\.[A-Za-z0-9._-]+'),
    re.compile(r'1//[A-Za-z0-9._-]+'),
    re.compile(r'("?(?:access_token|refresh_token)"?\s*[:=]\s*)"[^"]+"', re.IGNORECASE),
)


def redact_backup_output(value):
    if value is None:
        return ''
    if isinstance(value, bytes):
        text = value.decode('utf-8', errors='replace')
    else:
        text = str(value)
    for pattern in SECRET_PATTERNS:
        text = pattern.sub('<redacted>', text)
    return text


def run_command(args, timeout=60):
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=True,
    )


def get_backup_settings():
    return {
        'rclone_bin': os.environ.get('RCLONE_BIN', 'rclone'),
        'remote': os.environ.get('GDRIVE_REMOTE', 'gdrive'),
        'path': os.environ.get('GDRIVE_PATH', 'querolink-backups'),
        'script_path': os.environ.get('BACKUP_SCRIPT_PATH', '/app/scripts/backup.sh'),
        'check_timeout': int(os.environ.get('BACKUP_CHECK_TIMEOUT_SECONDS', '60')),
        'backup_timeout': int(os.environ.get('BACKUP_TIMEOUT_SECONDS', '900')),
    }


def run_backup_check(settings=None, runner=run_command):
    cfg = settings or get_backup_settings()
    rclone = cfg['rclone_bin']
    remote = cfg['remote']
    drive_path = cfg['path']
    timeout = cfg['check_timeout']
    remote_root = f'{remote}:'
    remote_dir = f'{remote}:{drive_path}'
    probe_name = f'.merito-backup-check-{os.getpid()}.txt'
    remote_probe = f'{remote_dir}/{probe_name}'

    steps = []

    def record(label, fn):
        result = fn()
        steps.append(label)
        return result

    record('rclone version', lambda: runner([rclone, 'version'], timeout=timeout))
    remotes = record('rclone listremotes', lambda: runner([rclone, 'listremotes'], timeout=timeout))
    if f'{remote}:' not in (remotes.stdout or '').splitlines():
        raise RuntimeError(f"Remote rclone '{remote}' nao configurado")

    record('rclone about', lambda: runner([rclone, 'about', remote_root], timeout=timeout))
    record('rclone mkdir', lambda: runner([rclone, 'mkdir', remote_dir], timeout=timeout))

    local_probe = None
    try:
        with tempfile.NamedTemporaryFile('w', delete=False, encoding='utf-8') as handle:
            handle.write('merito backup check\n')
            local_probe = handle.name
        record('rclone copyto probe', lambda: runner([rclone, 'copyto', local_probe, remote_probe], timeout=timeout))
        listed = record('rclone lsf probe', lambda: runner([rclone, 'lsf', remote_dir, '--include', probe_name], timeout=timeout))
        if probe_name not in (listed.stdout or ''):
            raise RuntimeError('Arquivo temporario de teste nao foi encontrado no Google Drive')
    finally:
        if local_probe:
            Path(local_probe).unlink(missing_ok=True)
        try:
            runner([rclone, 'deletefile', remote_probe], timeout=timeout)
        except Exception:
            pass

    return steps


def run_backup_now(settings=None, runner=run_command):
    cfg = settings or get_backup_settings()
    script_path = cfg['script_path']
    timeout = cfg['backup_timeout']
    if not Path(script_path).exists():
        raise FileNotFoundError(f'Script de backup nao encontrado: {script_path}')
    return runner([script_path], timeout=timeout)
