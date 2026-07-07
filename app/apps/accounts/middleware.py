import logging
from django.shortcuts import redirect
from django.http import JsonResponse
from django.core.cache import cache

logger = logging.getLogger(__name__)


class TrialEnforcementMiddleware:
    EXEMPT_PATHS = [
        '/dashboard/login/',
        '/dashboard/logout/',
        '/dashboard/plano-expirado/',
        '/dashboard/assinatura/',
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

            cache_key = f'tenant_operational:{tenant.uuid}'
            operational = cache.get(cache_key)
            if operational is None:
                from app.apps.accounts.models import tenant_operational
                operational = tenant_operational(tenant)
                cache.set(cache_key, operational, 60)

            if not operational:
                if not any(request.path.startswith(p) for p in self.EXEMPT_PATHS):
                    if request.path.startswith('/api/'):
                        return JsonResponse(
                            {'detail': 'Assinatura expirada ou conta suspensa.'},
                            status=402,
                        )
                    return redirect('dashboard:plano_expirado')

        return self.get_response(request)
