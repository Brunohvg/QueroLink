import json
from unittest.mock import patch, MagicMock
from django.test import TestCase, override_settings
from django.urls import reverse
from app.apps.accounts.models import Tenant, User
from app.apps.freight.correios_cws import CorreiosAuthClient
from app.apps.sellers.models import Seller


class FreightQuoteViewTest(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='Test Shop', slug='test-shop', is_active=True,
            store_cep='01001000',
        )
        self.user = User.objects.create_user(
            username='seller1', password='pass',
            role=User.Role.SELLER, tenant=self.tenant,
        )
        Seller.objects.create(
            tenant=self.tenant,
            user=self.user,
            name='Seller Test',
            phone='11999999999',
            is_active=True,
        )
        self.client.login(username='seller1', password='pass')

    def _post(self, data):
        return self.client.post(
            reverse('freight_quote'),
            data=json.dumps(data),
            content_type='application/json',
        )

    def test_no_store_cep_returns_400(self):
        self.tenant.store_cep = ''
        self.tenant.save()
        resp = self._post({'cep_destino': '01001000', 'weight_grams': 500})
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertFalse(data['success'])
        self.assertIn('configurar o CEP', data['error'])

    @patch('app.apps.freight.views.services.ViaCepClient.get_cep_info')
    @patch('app.apps.freight.views.services.estimate_correios')
    @patch('app.apps.freight.views.services.estimate_motoboy')
    def test_200_with_options(self, mock_motoboy, mock_correios, mock_cep):
        mock_cep.return_value = MagicMock(
            city='Sao Paulo', state='SP', neighborhood='Centro',
        )
        from app.apps.freight.services import FreightOption
        mock_correios.return_value = [
            FreightOption('PAC', 'PAC (estimativa)', 2200, 6),
            FreightOption('SEDEX', 'SEDEX (estimativa)', 3500, 2),
        ]
        mock_motoboy.return_value = None

        resp = self._post({'cep_destino': '01001000', 'weight_grams': 500})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['destination']['city'], 'Sao Paulo')
        self.assertEqual(len(data['options']), 2)
        self.assertEqual(data['options'][0]['label'], 'PAC (estimativa)')
        self.assertEqual(data['options'][1]['label'], 'SEDEX (estimativa)')
        self.assertIn('disclaimer', data)

    @patch.object(CorreiosAuthClient, 'get_token')
    @patch('app.apps.freight.correios_cws.requests.post')
    @patch('app.apps.freight.views.services.ViaCepClient.get_cep_info')
    @patch('app.apps.freight.views.services.estimate_motoboy')
    def test_official_quote_returns_correct_pac_and_sedex_labels(
        self, mock_motoboy, mock_cep, mock_requests, mock_get_token,
    ):
        self.tenant.correios_usuario = 'usr'
        self.tenant.correios_codigo_acesso = 'pass'
        self.tenant.save()
        mock_get_token.return_value = {'token': 'jwt-fake'}
        mock_cep.return_value = MagicMock(
            city='Sao Paulo', state='SP', neighborhood='Centro',
        )
        mock_motoboy.return_value = None

        mock_price = MagicMock(status_code=200)
        mock_price.json.return_value = [
            {'coProduto': '03298', 'pcFinal': '22,00'},
            {'coProduto': '03220', 'pcFinal': '35,00'},
        ]
        mock_prazo = MagicMock(status_code=200)
        mock_prazo.json.return_value = [
            {'coProduto': '03298', 'prazoEntrega': '6'},
            {'coProduto': '03220', 'prazoEntrega': '2'},
        ]
        mock_requests.side_effect = [mock_price, mock_prazo]

        resp = self._post({'cep_destino': '01001000', 'weight_grams': 500})

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        by_service = {item['service']: item for item in data['options']}
        self.assertEqual(by_service['03220']['label'], 'SEDEX')
        self.assertEqual(by_service['03298']['label'], 'PAC')
        self.assertTrue(by_service['03220']['official'])
        self.assertTrue(by_service['03298']['official'])

    def test_mobile_template_uses_option_label_not_pac_fallback_for_cws_codes(self):
        resp = self.client.get(reverse('dashboard:mobile_frete'))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'freightServiceLabel(opt)')
        self.assertContains(resp, "opt.service === '03220' || opt.service === 'SEDEX'")
        self.assertNotContains(resp, "opt.service === 'SEDEX' ? 'SEDEX' : 'PAC'")

    @patch.object(CorreiosAuthClient, 'get_token')
    @patch('app.apps.freight.correios_cws.requests.post')
    @patch('app.apps.freight.views.services.ViaCepClient.get_cep_info')
    @patch('app.apps.freight.views.services.estimate_motoboy')
    def test_quote_keeps_pac_visible_when_sedex_is_cheaper(
        self, mock_motoboy, mock_cep, mock_requests, mock_get_token,
    ):
        self.tenant.correios_usuario = 'usr'
        self.tenant.correios_codigo_acesso = 'pass'
        self.tenant.save()
        mock_get_token.return_value = {'token': 'jwt-fake'}
        mock_cep.return_value = MagicMock(
            city='Sao Paulo', state='SP', neighborhood='Centro',
        )
        mock_motoboy.return_value = None

        mock_price = MagicMock(status_code=200)
        mock_price.json.return_value = [
            {'coProduto': '03298', 'pcFinal': '40,00'},
            {'coProduto': '03220', 'pcFinal': '35,00'},
        ]
        mock_prazo = MagicMock(status_code=200)
        mock_prazo.json.return_value = [
            {'coProduto': '03298', 'prazoEntrega': '6'},
            {'coProduto': '03220', 'prazoEntrega': '2'},
        ]
        mock_requests.side_effect = [mock_price, mock_prazo]

        resp = self._post({'cep_destino': '01001000', 'weight_grams': 500})

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        by_service = {item['service']: item for item in data['options']}
        self.assertIn('03298', by_service)
        self.assertIn('03220', by_service)
        self.assertGreater(by_service['03298']['price_cents'], by_service['03220']['price_cents'])

    def test_mobile_template_highlights_sedex_when_it_is_best_option(self):
        resp = self.client.get(reverse('dashboard:mobile_frete'))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Nesta região, o SEDEX está mais vantajoso que o PAC.')
        self.assertContains(resp, 'Melhor opção')
        self.assertContains(resp, 'sedexBetterThanPac')
        self.assertContains(resp, 'isBestShippingOption(opt)')

    def test_invalid_cep_returns_400(self):
        resp = self._post({'cep_destino': 'abc', 'weight_grams': 500})
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertIn('CEP invalido', data['error'])

    def test_unauthenticated_returns_401(self):
        self.client.logout()
        resp = self._post({'cep_destino': '01001000', 'weight_grams': 500})
        self.assertEqual(resp.status_code, 401)
