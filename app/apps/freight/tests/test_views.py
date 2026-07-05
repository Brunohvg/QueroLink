import json
from unittest.mock import patch, MagicMock
from django.test import TestCase, override_settings
from django.urls import reverse
from app.apps.accounts.models import Tenant, User


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
        self.assertIn('disclaimer', data)

    def test_invalid_cep_returns_400(self):
        resp = self._post({'cep_destino': 'abc', 'weight_grams': 500})
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertIn('CEP invalido', data['error'])

    def test_unauthenticated_returns_401(self):
        self.client.logout()
        resp = self._post({'cep_destino': '01001000', 'weight_grams': 500})
        self.assertEqual(resp.status_code, 401)
