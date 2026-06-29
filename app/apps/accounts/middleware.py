import logging
from django.utils import timezone
from django.contrib import messages

logger = logging.getLogger(__name__)


class TrialEnforcementMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and hasattr(request.user, 'tenant'):
            tenant = request.user.tenant
            if tenant and not tenant.is_active:
                messages.warning(request, 'Sua conta esta suspensa. Entre em contato com o suporte.')
            elif tenant and tenant.trial_ends_at and tenant.trial_ends_at < timezone.now():
                exempt_paths = [
                    '/dashboard/gestor/configuracoes/',
                    '/dashboard/login/',
                    '/dashboard/logout/',
                    '/api/manager/webhook-status/',
                    '/admin/',
                    '/health/',
                ]
                if not any(request.path.startswith(p) for p in exempt_paths):
                    messages.warning(
                        request,
                        'Seu periodo de trial expirou. Atualize seu plano para continuar usando o sistema.'
                    )

        return self.get_response(request)
