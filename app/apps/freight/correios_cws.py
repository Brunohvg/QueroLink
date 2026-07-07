import logging
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal, ROUND_HALF_UP

import requests
from django.core.cache import cache

from .services import FreightOption

logger = logging.getLogger(__name__)

TOKEN_URL = 'https://api.correios.com.br/token/v1/autentica'
TOKEN_URL_CARTAO = 'https://api.correios.com.br/token/v1/autentica/cartaopostagem'
PRICE_URL = 'https://api.correios.com.br/preco/v1/nacional'
DEADLINE_URL = 'https://api.correios.com.br/prazo/v1/nacional'

DEFAULT_PRODUCTS = ['03298', '03220']
PRODUCT_LABELS = {'03298': 'PAC', '03220': 'SEDEX'}
DEFAULT_DIMENSIONS = {
    'psAltura': 20,
    'psLargura': 20,
    'psComprimento': 20,
}


class CorreiosAuthClient:

    def get_token(self, tenant) -> str | None:
        cache_key = f'correios_token_{tenant.pk}'
        cached = cache.get(cache_key)
        if cached:
            return cached

        token = self._authenticate(tenant)
        if token:
            exp = token.get('expiraEm') if isinstance(token, dict) else None
            if exp:
                try:
                    exp_dt = datetime.fromisoformat(str(exp).replace('Z', '+00:00'))
                    now = datetime.now(dt_timezone.utc)
                    ttl = max(int((exp_dt - now).total_seconds()) - 300, 60)
                except (ValueError, TypeError):
                    ttl = 300
            else:
                ttl = 300
            cache.set(cache_key, token, timeout=ttl)
        return token

    def _authenticate(self, tenant) -> dict | None:
        usuario = tenant.correios_usuario or ''
        codigo = tenant.correios_codigo_acesso or ''
        if not usuario or not codigo:
            return None

        cartao = tenant.correios_cartao or ''

        try:
            if cartao:
                url = TOKEN_URL_CARTAO
                body = {'numero': cartao}
            else:
                url = TOKEN_URL
                body = None

            resp = requests.post(
                url,
                auth=(usuario, codigo),
                json=body,
                timeout=10,
            )

            if resp.status_code == 401:
                logger.warning(
                    'Correios CWS: credenciais invalidas para tenant %s',
                    tenant.pk,
                )
                return None

            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.warning('Correios CWS auth failed for tenant %s: %s', tenant.pk, e)
            return None


class CorreiosPricingClient:

    def __init__(self, token: dict, contrato: str = '', cartao: str = ''):
        self._token = token
        self._contrato = contrato
        self._cartao = cartao

    @property
    def _bearer(self) -> str | None:
        if isinstance(self._token, dict):
            return self._token.get('token')
        return self._token

    @property
    def _headers(self):
        return {
            'Authorization': f'Bearer {self._bearer}',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }

    def calculate_batch(self, cep_origem: str, cep_destino: str,
                        peso_gramas: int) -> list[FreightOption]:
        if not self._bearer:
            return []

        co_produtos = DEFAULT_PRODUCTS
        id_lote = 'lote-merito-001'
        peso_str = str(max(peso_gramas, 1))

        price_payload = {
            'idLote': id_lote,
            'parametrosProduto': [
                {
                    'coProduto': p,
                    'nuRequisicao': f'preco-{p}',
                    'cepOrigem': cep_origem,
                    'cepDestino': cep_destino,
                    'psObjeto': peso_str,
                    **DEFAULT_DIMENSIONS,
                    **({'nuContrato': self._contrato} if self._contrato else {}),
                    **({'nuDR': self._cartao} if self._cartao else {}),
                }
                for p in co_produtos
            ],
        }

        deadline_payload = {
            'idLote': id_lote,
            'parametrosProduto': [
                {
                    'coProduto': p,
                    'nuRequisicao': f'prazo-{p}',
                    'cepOrigem': cep_origem,
                    'cepDestino': cep_destino,
                    'dtEvento': datetime.now(dt_timezone.utc).strftime('%d-%m-%Y'),
                }
                for p in co_produtos
            ],
        }

        prices = self._fetch(PRICE_URL, price_payload)
        deadlines = self._fetch(DEADLINE_URL, deadline_payload)

        price_map = {}
        for item in prices:
            co = item.get('coProduto', '')
            pc_final = item.get('pcFinal', '')
            if not pc_final:
                continue
            try:
                price_map[co] = int(Decimal(str(pc_final).replace(',', '.'))
                                    .quantize(Decimal('0.01'), rounding=ROUND_HALF_UP) * 100)
            except Exception:
                logger.warning('CWS: preco invalido para produto %s', co)

        deadline_map = {}
        for item in deadlines:
            co = item.get('coProduto', '')
            prazo = item.get('prazoEntrega')
            if prazo is not None:
                try:
                    deadline_map[co] = int(prazo)
                except (ValueError, TypeError):
                    pass

        def _msg_erro(items):
            for item in items:
                msg = item.get('msgErro', '')
                if msg:
                    return msg
            return None

        result = []
        for p in co_produtos:
            err = _msg_erro([pi for pi in prices if pi.get('coProduto') == p])
            if err:
                continue
            price = price_map.get(p)
            if not price or price <= 0:
                continue
            days = deadline_map.get(p, 0)
            label = PRODUCT_LABELS.get(p, p)
            result.append(FreightOption(p, label, price, days, official=True))
        return result

    def _fetch(self, url, payload):
        try:
            resp = requests.post(url, json=payload, headers=self._headers, timeout=10)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.warning('CWS fetch failed for %s: %s', url, e)
            return []
