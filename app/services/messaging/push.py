import json
import logging

from django.conf import settings

logger = logging.getLogger(__name__)


def send_push_notification(user, title, body, url=None, icon=None):
    if not settings.VAPID_PRIVATE_KEY:
        return

    from app.apps.notifications.models import PushSubscription

    subscriptions = PushSubscription.objects.filter(
        user=user, is_active=True,
    ).select_related('tenant')

    claims = {'sub': f'mailto:{settings.VAPID_CONTACT_EMAIL}'}
    icon = icon or '/static/icons/icon-192.png'

    payload = json.dumps({
        'title': title,
        'body': body,
        'url': url or '/dashboard/mobile/',
        'icon': icon,
    })

    for sub in subscriptions:
        subscription_info = {
            'endpoint': sub.endpoint,
            'keys': {'p256dh': sub.p256dh, 'auth': sub.auth},
        }
        try:
            from pywebpush import webpush, WebPushException
            webpush(
                subscription_info=subscription_info,
                data=payload,
                vapid_private_key=settings.VAPID_PRIVATE_KEY,
                vapid_claims=claims,
                timeout=10,
            )
        except WebPushException as e:
            if e.response and e.response.status_code in (404, 410):
                logger.info(
                    "Push endpoint gone, marking inactive: user=%s endpoint=%s",
                    user.id, sub.endpoint[:50],
                )
                sub.is_active = False
                sub.save(update_fields=['is_active'])
            else:
                logger.warning("Push notification failed for user=%s: %s", user.id, e)
        except Exception as e:
            logger.warning("Push notification network error for user=%s: %s", user.id, e)
