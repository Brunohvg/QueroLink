import logging
import os
import re
import subprocess

from celery import shared_task

logger = logging.getLogger(__name__)

_SECRET_PATTERNS = (
    re.compile(r'ya29\.[A-Za-z0-9._-]+'),
    re.compile(r'1//[A-Za-z0-9._-]+'),
    re.compile(r'("?(?:access_token|refresh_token)"?\s*[:=]\s*)"[^"]+"', re.IGNORECASE),
)


def _redact_backup_output(value):
    if value is None:
        return ''
    if isinstance(value, bytes):
        text = value.decode('utf-8', errors='replace')
    else:
        text = str(value)
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub('<redacted>', text)
    return text


def _tail_lines(value, limit=20):
    text = _redact_backup_output(value).strip()
    if not text:
        return []
    return text.split('\n')[-limit:]


@shared_task(soft_time_limit=960, time_limit=1020)
def daily_backup():
    script_path = os.environ.get('BACKUP_SCRIPT_PATH', '/app/scripts/backup.sh')
    timeout = int(os.environ.get('BACKUP_TIMEOUT_SECONDS', '900'))
    try:
        result = subprocess.run(
            [script_path],
            capture_output=True, text=True, timeout=timeout,
            check=True,
        )
        stdout_lines = _tail_lines(result.stdout, limit=1)
        logger.info("Backup concluido: %s", stdout_lines[-1] if stdout_lines else 'sem stdout')
    except subprocess.CalledProcessError as e:
        for line in _tail_lines(e.stdout):
            logger.info("backup: %s", line)
        stderr = _redact_backup_output(e.stderr).strip()
        if stderr:
            logger.error("Backup stderr (ultimos 1000 chars): %s", stderr[-1000:])
        raise
    except subprocess.TimeoutExpired as e:
        stdout = _redact_backup_output(e.stdout).strip()
        if stdout:
            logger.error("Backup timeout apos %ss — ultimos 500 chars: %s", timeout, stdout[-500:])
        else:
            logger.error("Backup timeout apos %ss — sem stdout", timeout)
        raise
    except FileNotFoundError:
        logger.exception("Script de backup nao encontrado: %s", script_path)
        raise
