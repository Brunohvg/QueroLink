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


class _BoletoLookupThrottle(SimpleRateThrottle):
    def get_cache_key(self, request, view):
        if not request.user.is_authenticated:
            return None
        return self.cache_format % {
            'scope': self.scope,
            'ident': f'{request.user.tenant_id}:{request.user.pk}',
        }


class CnpjLookupThrottle(_BoletoLookupThrottle):
    scope = 'boleto_lookup_cnpj'


class CepLookupThrottle(_BoletoLookupThrottle):
    scope = 'boleto_lookup_cep'


class CustomerLookupThrottle(_BoletoLookupThrottle):
    scope = 'boleto_lookup_customer'
