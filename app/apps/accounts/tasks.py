import logging
import subprocess

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(soft_time_limit=360, time_limit=420)
def daily_backup():
    try:
        result = subprocess.run(
            ['/app/scripts/backup.sh'],
            capture_output=True, text=True, timeout=300,
            check=True,
        )
        logger.info("Backup concluido: %s", result.stdout.strip().split('\n')[-1])
    except subprocess.CalledProcessError as e:
        if e.stdout:
            for line in e.stdout.strip().split('\n')[-20:]:
                logger.info("backup: %s", line)
        if e.stderr:
            logger.error("Backup stderr (ultimos 1000 chars): %s", e.stderr.strip()[-1000:])
        raise
    except subprocess.TimeoutExpired as e:
        if e.stdout:
            logger.error("Backup timeout — ultimos 500 chars: %s",
                          e.stdout.decode('utf-8', errors='replace')[-500:])
        else:
            logger.error("Backup timeout apos 300s — sem stdout")
        raise
    except FileNotFoundError:
        logger.exception("Script de backup nao encontrado: /app/scripts/backup.sh")
        raise
