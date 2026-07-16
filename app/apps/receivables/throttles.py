from rest_framework.throttling import SimpleRateThrottle, UserRateThrottle


class BoletoCreateThrottle(UserRateThrottle):
    scope = 'boleto_create'


class BoletoResendThrottle(SimpleRateThrottle):
    scope = 'boleto_resend'

    def get_cache_key(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return None
        lookup_kwarg = view.lookup_url_kwarg or view.lookup_field
        boleto_uuid = view.kwargs.get(lookup_kwarg, '')
        return self.cache_format % {
            'scope': self.scope,
            'ident': f'{request.user.pk}:{boleto_uuid}',
        }
