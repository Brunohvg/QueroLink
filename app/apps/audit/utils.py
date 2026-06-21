import logging

from app.apps.audit.models import AuditLog

logger = logging.getLogger(__name__)


def log_action(request, action, instance=None, changes=None):
    try:
        AuditLog.objects.create(
            user=request.user if request.user.is_authenticated else None,
            action=action,
            model_name=instance.__class__.__name__ if instance else None,
            object_id=str(getattr(instance, 'uuid', getattr(instance, 'pk', ''))) if instance else None,
            changes=changes or {},
            ip_address=request.META.get('REMOTE_ADDR'),
        )
    except Exception:
        logger.exception('Failed to write audit log')
