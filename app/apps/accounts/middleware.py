import logging
from django.utils import timezone
from django.shortcuts import redirect
from django.http import JsonResponse

logger = logging.getLogger(__name__)


class TrialEnforcementMiddleware:
    EXEMPT_PATHS = [
        '/dashboard/login/',
        '/dashboard/logout/',
        '/dashboard/plano-expirado/',
        '/dashboard/gestor/configuracoes/',
        '/api/manager/webhook-status/',
        '/api/webhooks/',
        '/admin/',
        '/health/',
        '/static/',
    ]

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (
            request.user.is_authenticated
            and hasattr(request.user, 'tenant')
            and request.user.tenant
            and not request.user.is_superuser
        ):
            tenant = request.user.tenant
            blocked = False
            reason = ''

            if not tenant.is_active:
                blocked = True
                reason = 'Sua conta esta suspensa. Entre em contato com o suporte.'
            elif tenant.trial_ends_at and tenant.trial_ends_at < timezone.now():
                blocked = True
                reason = 'Seu periodo de trial expirou. Atualize seu plano para continuar usando o sistema.'

            if blocked and not any(request.path.startswith(p) for p in self.EXEMPT_PATHS):
                if request.path.startswith('/api/'):
                    return JsonResponse(
                        {'detail': 'Assinatura expirada ou conta suspensa.'},
                        status=402,
                    )
                return redirect('dashboard:plano_expirado')

        return self.get_response(request)
