from unittest.mock import patch, MagicMock
from django.test import TestCase

from app.apps.freight import services


class EstimateCorreiosTest(TestCase):

    def test_sp_range_base_prices(self):
        result = services.estimate_correios('01000000', weight_grams=300)
        pac, sedex = result[0], result[1]
        self.assertEqual(pac.service, 'PAC')
        self.assertEqual(pac.price_cents, 2200)
        self.assertEqual(pac.delivery_days, 6)
        self.assertEqual(sedex.service, 'SEDEX')
        self.assertEqual(sedex.price_cents, 3500)
        self.assertEqual(sedex.delivery_days, 2)

    def test_weight_1kg_adds_extra(self):
        pac_300g = services.estimate_correios('01000000', weight_grams=300)[0]
        pac_1kg = services.estimate_correios('01000000', weight_grams=1000)[0]
        self.assertGreater(pac_1kg.price_cents, pac_300g.price_cents)

    def test_adjustment_plus_10_percent(self):
        result = services.estimate_correios('01000000', weight_grams=300, adjustment_percent=10)
        pac = result[0]
        expected = int(22.0 * 1.10 * 100)
        self.assertEqual(pac.price_cents, expected)

    def test_invalid_cep_returns_error(self):
        result = services.estimate_correios('abc', weight_grams=300)
        self.assertIn('CEP invalido', result[0].error)
        self.assertIn('CEP invalido', result[1].error)

    def test_rj_range(self):
        result = services.estimate_correios('20000000', weight_grams=300)
        self.assertEqual(result[0].price_cents, 2400)
        self.assertEqual(result[1].price_cents, 3800)

    def test_unknown_cep_uses_default(self):
        result = services.estimate_correios('99999999', weight_grams=300)
        self.assertEqual(result[0].price_cents, 3200)
        self.assertEqual(result[1].price_cents, 5000)


class ViaCepClientTest(TestCase):

    def setUp(self):
        self.client = services.ViaCepClient()

    @patch.object(services.cache, 'get', return_value=None)
    @patch.object(services.cache, 'set')
    @patch('app.apps.freight.services.requests.get')
    def test_viacep_success(self, mock_get, mock_cache_set, mock_cache_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                'logradouro': 'Rua Teste',
                'bairro': 'Centro',
                'localidade': 'Sao Paulo',
                'uf': 'SP',
            },
            raise_for_status=MagicMock(),
        )
        info = self.client.get_cep_info('01001000')
        self.assertIsNotNone(info)
        self.assertEqual(info.city, 'Sao Paulo')
        self.assertEqual(info.state, 'SP')
        mock_cache_set.assert_called_once()

    @patch.object(services.cache, 'get', return_value=None)
    @patch.object(services.cache, 'set')
    @patch('app.apps.freight.services.requests.get')
    def test_viacep_fallback_to_brasilapi(self, mock_get, mock_cache_set, mock_cache_get):
        call_count = [0]

        def side_effect(url, *args, **kwargs):
            call_count[0] += 1
            resp = MagicMock(raise_for_status=MagicMock())
            if 'viacep' in url:
                resp.status_code = 500
                resp.raise_for_status.side_effect = Exception('down')
            else:
                resp.status_code = 200
                resp.json = lambda: {
                    'street': 'Av Brasil',
                    'neighborhood': 'Jardins',
                    'city': 'Rio de Janeiro',
                    'state': 'RJ',
                }
            return resp

        mock_get.side_effect = side_effect
        info = self.client.get_cep_info('20040000')
        self.assertIsNotNone(info)
        self.assertEqual(info.city, 'Rio de Janeiro')
        self.assertEqual(call_count[0], 2)

    @patch.object(services.cache, 'get')
    def test_cache_hit_stops_http(self, mock_cache_get):
        mock_cache_get.return_value = {
            'cep': '01001000', 'street': '', 'neighborhood': '',
            'city': 'Cached City', 'state': 'SP',
        }
        info = self.client.get_cep_info('01001000')
        self.assertIsNotNone(info)
        self.assertEqual(info.city, 'Cached City')


class HaversineTest(TestCase):

    def test_same_point_zero_distance(self):
        d = services.haversine_distance(-23.55, -46.63, -23.55, -46.63)
        self.assertEqual(d, 0.0)

    def test_sp_to_rj_approximate(self):
        d = services.haversine_distance(-23.55, -46.63, -22.91, -43.20)
        self.assertGreater(d, 300)
        self.assertLess(d, 500)
