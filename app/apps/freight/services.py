import logging
import math
from dataclasses import dataclass, asdict
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

import requests
from django.core.cache import cache

logger = logging.getLogger(__name__)


@dataclass
class CepInfo:
    cep: str
    street: str
    neighborhood: str
    city: str
    state: str


@dataclass
class FreightOption:
    service: str
    label: str
    price_cents: int
    delivery_days: int
    error: Optional[str] = None
    official: bool = False


class ViaCepClient:
    VIACEP_URL = 'https://viacep.com.br/ws'
    BRASILAPI_URL = 'https://brasilapi.com.br/api/cep/v1'

    def get_cep_info(self, cep: str) -> Optional[CepInfo]:
        digits = ''.join(filter(str.isdigit, cep or ''))
        if len(digits) != 8:
            return None
        cache_key = f'freight_cep_{digits}'
        cached = cache.get(cache_key)
        if cached:
            return CepInfo(**cached)
        info = self._try_viacep(digits) or self._try_brasilapi(digits)
        if info:
            cache.set(cache_key, asdict(info), timeout=7 * 24 * 3600)
        return info

    def _try_viacep(self, cep):
        try:
            r = requests.get(f'{self.VIACEP_URL}/{cep}/json/', timeout=5)
            r.raise_for_status()
            data = r.json()
            if data.get('erro'):
                return None
            return CepInfo(
                cep=cep, street=data.get('logradouro', ''),
                neighborhood=data.get('bairro', ''),
                city=data.get('localidade', ''), state=data.get('uf', ''),
            )
        except Exception as e:
            logger.warning('ViaCEP falhou: %s', e)
            return None

    def _try_brasilapi(self, cep):
        try:
            r = requests.get(f'{self.BRASILAPI_URL}/{cep}', timeout=5)
            r.raise_for_status()
            data = r.json()
            return CepInfo(
                cep=cep, street=data.get('street', ''),
                neighborhood=data.get('neighborhood', ''),
                city=data.get('city', ''), state=data.get('state', ''),
            )
        except Exception as e:
            logger.warning('BrasilAPI falhou: %s', e)
            return None


CORREIOS_PRICE_TABLE = {
    (1000000, 19999999): (35.0, 22.0, 2, 6),
    (20000000, 28999999): (38.0, 24.0, 2, 7),
    (29000000, 29999999): (40.0, 25.0, 3, 8),
    (30000000, 39999999): (36.0, 23.0, 2, 6),
    (40000000, 48999999): (45.0, 28.0, 4, 10),
    (49000000, 49999999): (47.0, 29.0, 5, 11),
    (50000000, 56999999): (48.0, 30.0, 5, 12),
    (57000000, 57999999): (50.0, 32.0, 5, 12),
    (58000000, 58999999): (48.0, 30.0, 5, 12),
    (59000000, 59999999): (50.0, 32.0, 6, 14),
    (60000000, 63999999): (52.0, 34.0, 6, 14),
    (64000000, 64999999): (55.0, 36.0, 7, 15),
    (65000000, 65999999): (55.0, 36.0, 7, 15),
    (66000000, 68899999): (58.0, 38.0, 8, 18),
    (68900000, 68999999): (60.0, 40.0, 10, 20),
    (69000000, 69299999): (60.0, 40.0, 10, 20),
    (69300000, 69399999): (65.0, 45.0, 12, 25),
    (69400000, 69899999): (60.0, 40.0, 10, 20),
    (69900000, 69999999): (62.0, 42.0, 10, 22),
    (70000000, 73699999): (42.0, 26.0, 3, 8),
    (73700000, 76799999): (45.0, 28.0, 4, 10),
    (76800000, 76999999): (55.0, 36.0, 6, 14),
    (77000000, 77999999): (50.0, 32.0, 5, 12),
    (78000000, 78899999): (48.0, 30.0, 5, 12),
    (78900000, 78999999): (48.0, 30.0, 5, 12),
    (79000000, 79999999): (45.0, 28.0, 4, 10),
    (80000000, 87999999): (40.0, 25.0, 3, 8),
    (88000000, 89999999): (42.0, 26.0, 3, 8),
    (90000000, 99999999): (45.0, 28.0, 4, 10),
}
DEFAULT_PRICE_ROW = (50.0, 32.0, 5, 12)


def _lookup_cep_row(cep_num: int):
    for (lo, hi), values in CORREIOS_PRICE_TABLE.items():
        if lo <= cep_num <= hi:
            return values
    return DEFAULT_PRICE_ROW


def estimate_correios(cep_destino_digits: str, weight_grams: int, adjustment_percent: int = 0) -> list:
    try:
        cep_num = int(cep_destino_digits)
    except (TypeError, ValueError):
        return [
            FreightOption('PAC', 'PAC (estimativa)', 0, 0, error='CEP invalido'),
            FreightOption('SEDEX', 'SEDEX (estimativa)', 0, 0, error='CEP invalido'),
        ]

    sedex_base, pac_base, sedex_days, pac_days = _lookup_cep_row(cep_num)

    weight_kg = max(weight_grams, 100) / 1000.0
    extra_kg = max(0.0, weight_kg - 0.3)

    def _cents(base, extra_per_kg):
        price = base + extra_kg * extra_per_kg
        price *= (1 + adjustment_percent / 100.0)
        dec = Decimal(str(price)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        return int(dec * 100)

    return [
        FreightOption('PAC', 'PAC (estimativa)', _cents(pac_base, 5.0), pac_days),
        FreightOption('SEDEX', 'SEDEX (estimativa)', _cents(sedex_base, 8.0), sedex_days),
    ]


# ── Motoboy ──────────────────────────────────────────────

_NOMINATIM_URL = 'https://nominatim.openstreetmap.org/search'
_PHOTON_URL = 'https://photon.komoot.io/api/'
_USER_AGENT = 'Merito/1.0'


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def _geocode(query: str) -> Optional[tuple]:
    def _nominatim(q):
        try:
            r = requests.get(_NOMINATIM_URL, params={
                'q': q, 'format': 'json', 'limit': 1,
                'countrycodes': 'br',
            }, headers={'User-Agent': _USER_AGENT}, timeout=5)
            r.raise_for_status()
            results = r.json()
            if results:
                return float(results[0]['lat']), float(results[0]['lon'])
        except Exception as e:
            logger.warning('Nominatim falhou para %s: %s', q, e)
        return None

    def _photon(q):
        try:
            r = requests.get(_PHOTON_URL, params={'q': q, 'limit': 1}, timeout=5)
            r.raise_for_status()
            data = r.json()
            features = data.get('features', [])
            if features:
                props = features[0].get('geometry', {}).get('coordinates', [])
                if len(props) >= 2:
                    return float(props[1]), float(props[0])
        except Exception as e:
            logger.warning('Photon falhou para %s: %s', q, e)
        return None

    return _nominatim(query) or _photon(query)


def _get_store_coords(tenant) -> Optional[tuple]:
    cache_key = f'freight_store_geo_{tenant.pk}'
    cached = cache.get(cache_key)
    if cached:
        return tuple(cached)
    store_cep = tenant.store_cep or ''
    digits = ''.join(filter(str.isdigit, store_cep))
    if len(digits) != 8:
        return None
    info = ViaCepClient().get_cep_info(digits)
    if not info:
        return None
    query = f'{info.street}, {info.city}, {info.state}'
    coords = _geocode(query)
    if coords:
        cache.set(cache_key, list(coords), timeout=30 * 24 * 3600)
    return coords


def estimate_motoboy(tenant, cep_info: CepInfo) -> Optional[FreightOption]:
    if not getattr(tenant, 'motoboy_enabled', False):
        return None
    store_coords = _get_store_coords(tenant)
    if not store_coords:
        return FreightOption('MOTOBOY', 'Motoboy', 0, 0,
                             error='Endereco da loja nao configurado')
    dest_query = f'{cep_info.street}, {cep_info.city}, {cep_info.state}'
    dest_coords = _geocode(dest_query)
    if not dest_coords:
        return FreightOption('MOTOBOY', 'Motoboy', 0, 0,
                             error='Nao foi possivel localizar o endereco')
    distance = haversine_distance(*store_coords, *dest_coords) * 1.3
    max_km = getattr(tenant, 'motoboy_max_km', 0) or 0
    if max_km and distance > max_km:
        return FreightOption('MOTOBOY', 'Motoboy', 0, 0,
                             error='Fora da area de entrega')
    price_per_km = getattr(tenant, 'motoboy_price_per_km_cents', None)
    if price_per_km is None:
        price_per_km = 200
    min_price = getattr(tenant, 'motoboy_min_price_cents', None)
    if min_price is None:
        min_price = 800
    price = max(round(distance * price_per_km), min_price)
    return FreightOption('MOTOBOY', 'Motoboy', int(price), 0)


DEFAULT_PRESETS = [
    {'name': 'Envelope', 'weight_grams': 100},
    {'name': 'Caixa P', 'weight_grams': 500},
    {'name': 'Caixa M', 'weight_grams': 1000},
]


def get_freight_presets(tenant):
    presets = getattr(tenant, 'freight_presets', None)
    if isinstance(presets, list) and len(presets) > 0:
        return presets
    return DEFAULT_PRESETS
