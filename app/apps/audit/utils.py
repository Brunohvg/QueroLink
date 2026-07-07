import logging

from app.apps.audit.models import AuditLog

logger = logging.getLogger(__name__)


def log_action(request, action, instance=None, changes=None):
    try:
        user = None
        tenant = None
        ip_address = None

        if request is not None:
            if hasattr(request, 'user'):
                user = request.user if request.user.is_authenticated else None
                ip_address = request.META.get('REMOTE_ADDR')
            elif hasattr(request, 'tenant_id'):
                user = request
            if user and user.tenant_id:
                tenant = user.tenant

        AuditLog.objects.create(
            user=user,
            tenant=tenant,
            action=action,
            model_name=instance.__class__.__name__ if instance else None,
            object_id=str(getattr(instance, 'uuid', getattr(instance, 'pk', ''))) if instance else None,
            changes=changes or {},
            ip_address=ip_address,
        )
    except Exception:
        logger.exception('Erro ao registrar audit log')
