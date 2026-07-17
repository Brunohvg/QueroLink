from rest_framework.throttling import SimpleRateThrottle


class BoletoCreateThrottle(SimpleRateThrottle):
    scope = 'boleto_create'

    def get_cache_key(self, request, view):
        if not request.user.is_authenticated:
            return None
        return self.cache_format % {
            'scope': self.scope,
            'ident': request.user.pk,
        }


class BoletoCancelThrottle(SimpleRateThrottle):
    scope = 'boleto_cancel'

    def get_cache_key(self, request, view):
        if not request.user.is_authenticated:
            return None
        return self.cache_format % {
            'scope': self.scope,
            'ident': request.user.pk,
        }
