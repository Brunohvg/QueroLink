from django.utils import timezone
from django.conf import settings


def trial_status(request):
    if not request.user.is_authenticated:
        return {'trial_days_left': None, 'show_trial_banner': False}

    tenant = getattr(request.user, 'tenant', None)
    if not tenant:
        return {'trial_days_left': None, 'show_trial_banner': False}

    from app.apps.billing.models import Subscription
    try:
        sub = Subscription.objects.get(tenant=tenant)
        if sub.status == 'ACTIVE':
            return {'trial_days_left': None, 'show_trial_banner': False}
    except Subscription.DoesNotExist:
        pass

    if not tenant.trial_ends_at:
        return {'trial_days_left': None, 'show_trial_banner': False}

    remaining = (tenant.trial_ends_at - timezone.now()).days
    if remaining <= 7 and remaining >= 0:
        if request.COOKIES.get('trial_banner_dismissed'):
            return {'trial_days_left': None, 'show_trial_banner': False}
        return {
            'trial_days_left': remaining,
            'show_trial_banner': True,
        }

    return {'trial_days_left': None, 'show_trial_banner': False}


def global_context(request):
    return {
        'VAPID_PUBLIC_KEY': getattr(settings, 'VAPID_PUBLIC_KEY', ''),
    }
