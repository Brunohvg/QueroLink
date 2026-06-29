import logging
import subprocess

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task
def daily_backup():
    try:
        result = subprocess.run(
            ['/app/scripts/backup.sh'],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode == 0:
            logger.info("Backup concluído: %s", result.stdout.strip().split('\n')[-1])
        else:
            logger.error("Backup falhou (código %d): %s", result.returncode, result.stderr[-500:])
    except subprocess.TimeoutExpired:
        logger.error("Backup timeout após 300s")
    except FileNotFoundError:
        logger.error("Script de backup não encontrado: /app/scripts/backup.sh")
    except Exception as e:
        logger.error("Backup exception: %s", e)
