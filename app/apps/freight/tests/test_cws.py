import json
from unittest.mock import patch, MagicMock
from django.test import TestCase
from django.urls import reverse

from app.apps.accounts.models import Tenant, User
from app.apps.freight.views import _get_correios_options
from app.apps.freight.correios_cws import CorreiosAuthClient, CorreiosPricingClient


class GetCorreiosOptionsTest(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='CWS Test', slug='cws-test', is_active=True,
            store_cep='01001000', freight_adjustment_percent=10,
        )

    def test_no_credentials_uses_table(self):
        options, official = _get_correios_options(self.tenant, '20040000', 500)
        self.assertFalse(official)
        self.assertGreater(len(options), 0)
        self.assertEqual(options[0].official, False)

    def test_no_credentials_includes_ajuste(self):
        options, _ = _get_correios_options(self.tenant, '01000000', 300)
        base = 2200
        with_ajuste = int(22.0 * 1.10 * 100)
        self.assertEqual(options[0].price_cents, with_ajuste)

    @patch('app.apps.freight.correios_cws.requests.post')
    @patch.object(CorreiosAuthClient, 'get_token')
    def test_cws_success_returns_official(self, mock_get_token, mock_requests):
        self.tenant.correios_usuario = 'usr'
        self.tenant.correios_codigo_acesso = 'pass123'
        self.tenant.save()

        mock_get_token.return_value = {'token': 'jwt-fake', 'expiraEm': '2099-01-01T00:00:00Z'}

        mock_price = MagicMock(status_code=200)
        mock_price.json.return_value = [
            {'coProduto': '03298', 'pcFinal': '15,50'},
            {'coProduto': '03220', 'pcFinal': '28,90'},
        ]
        mock_prazo = MagicMock(status_code=200)
        mock_prazo.json.return_value = [
            {'coProduto': '03298', 'prazoEntrega': '5'},
            {'coProduto': '03220', 'prazoEntrega': '2'},
        ]
        mock_requests.side_effect = [mock_price, mock_prazo]

        options, official = _get_correios_options(self.tenant, '20040000', 500)
        self.assertTrue(official)
        self.assertGreater(len(options), 0)
        for o in options:
            self.assertTrue(o.official)
            self.assertNotIn('estimativa', o.label)

    @patch('app.apps.freight.correios_cws.requests.post')
    def test_price_payload_uses_official_package_fields(self, mock_requests):
        mock_price = MagicMock(status_code=200)
        mock_price.json.return_value = [{'coProduto': '03220', 'pcFinal': '28,90'}]
        mock_prazo = MagicMock(status_code=200)
        mock_prazo.json.return_value = [{'coProduto': '03220', 'prazoEntrega': '2'}]
        mock_requests.side_effect = [mock_price, mock_prazo]

        CorreiosPricingClient(
            token={'token': 'jwt-fake'},
            contrato='1234567890',
            dr='72',
        ).calculate_batch('01001000', '20040000', 500)

        payload = mock_requests.call_args_list[0].kwargs['json']
        item = payload['parametrosProduto'][0]
        self.assertEqual(item['tpObjeto'], '2')
        self.assertEqual(item['comprimento'], '20')
        self.assertEqual(item['largura'], '20')
        self.assertEqual(item['altura'], '20')
        self.assertNotIn('psAltura', item)

    @patch('app.apps.freight.correios_cws.requests.post')
    def test_nu_dr_is_omitted_without_resolution_and_sent_when_resolved(self, mock_requests):
        mock_price = MagicMock(status_code=200)
        mock_price.json.return_value = [{'coProduto': '03220', 'pcFinal': '28,90'}]
        mock_prazo = MagicMock(status_code=200)
        mock_prazo.json.return_value = [{'coProduto': '03220', 'prazoEntrega': '2'}]
        mock_requests.side_effect = [mock_price, mock_prazo, mock_price, mock_prazo]

        CorreiosPricingClient(
            token={'token': 'jwt-fake'},
            contrato='1234567890',
        ).calculate_batch('01001000', '20040000', 500)
        item_without_dr = mock_requests.call_args_list[0].kwargs['json']['parametrosProduto'][0]
        self.assertNotIn('nuDR', item_without_dr)

        CorreiosPricingClient(
            token={'token': 'jwt-fake', '_resolved_dr': '72'},
            contrato='1234567890',
        ).calculate_batch('01001000', '20040000', 500)
        item_with_dr = mock_requests.call_args_list[2].kwargs['json']['parametrosProduto'][0]
        self.assertEqual(item_with_dr['nuDR'], '72')
        self.assertNotEqual(item_with_dr['nuDR'], '0076666666')

    @patch('app.apps.freight.correios_cws.requests.post')
    def test_deadline_payload_uses_parametros_prazo_and_iso_posting_date(self, mock_requests):
        mock_price = MagicMock(status_code=200)
        mock_price.json.return_value = [{'coProduto': '03220', 'pcFinal': '28,90'}]
        mock_prazo = MagicMock(status_code=200)
        mock_prazo.json.return_value = [{'coProduto': '03220', 'prazoEntrega': '2'}]
        mock_requests.side_effect = [mock_price, mock_prazo]

        CorreiosPricingClient(token={'token': 'jwt-fake'}).calculate_batch('01001000', '20040000', 500)

        payload = mock_requests.call_args_list[1].kwargs['json']
        item = payload['parametrosPrazo'][0]
        self.assertIn('dataPostagem', item)
        self.assertRegex(item['dataPostagem'], r'^\d{4}-\d{2}-\d{2}$')
        self.assertNotIn('parametrosProduto', payload)

    @patch.object(CorreiosAuthClient, 'get_token')
    def test_token_401_falls_back_to_table(self, mock_get_token):
        self.tenant.correios_usuario = 'usr'
        self.tenant.correios_codigo_acesso = 'bad'
        self.tenant.save()

        mock_get_token.return_value = None

        options, official = _get_correios_options(self.tenant, '20040000', 500)
        self.assertFalse(official)
        self.assertGreater(len(options), 0)

    @patch.object(CorreiosAuthClient, 'get_token')
    @patch('app.apps.freight.correios_cws.requests.post')
    def test_batch_msg_erro_discards_product(self, mock_requests, mock_get_token):
        self.tenant.correios_usuario = 'usr'
        self.tenant.correios_codigo_acesso = 'pass123'
        self.tenant.save()

        mock_get_token.return_value = {'token': 'jwt-fake'}
        mock_price = MagicMock(status_code=200)
        mock_price.json.return_value = [
            {'coProduto': '03298', 'pcFinal': '15,50'},
            {'coProduto': '03220', 'pcFinal': '0,00', 'msgErro': 'Servico indisponivel'},
        ]
        mock_prazo = MagicMock(status_code=200)
        mock_prazo.json.return_value = [
            {'coProduto': '03298', 'prazoEntrega': '5'},
            {'coProduto': '03220', 'prazoEntrega': '0'},
        ]
        mock_requests.side_effect = [mock_price, mock_prazo]

        options, official = _get_correios_options(self.tenant, '20040000', 500)
        self.assertTrue(official)
        self.assertEqual(len(options), 1)
        self.assertEqual(options[0].service, '03298')

    @patch.object(CorreiosAuthClient, 'get_token')
    @patch('app.apps.freight.correios_cws.requests.post')
    def test_empty_deadline_preserves_price_with_zero_days(self, mock_requests, mock_get_token):
        self.tenant.correios_usuario = 'usr'
        self.tenant.correios_codigo_acesso = 'pass123'
        self.tenant.save()

        mock_get_token.return_value = {'token': 'jwt-fake'}
        mock_price = MagicMock(status_code=200)
        mock_price.json.return_value = [{'coProduto': '03220', 'pcFinal': '28,90'}]
        mock_prazo = MagicMock(status_code=200)
        mock_prazo.json.return_value = []
        mock_requests.side_effect = [mock_price, mock_prazo]

        options, official = _get_correios_options(self.tenant, '20040000', 500)
        self.assertTrue(official)
        self.assertEqual(len(options), 1)
        self.assertEqual(options[0].service, '03220')
        self.assertEqual(options[0].delivery_days, 0)

    @patch.object(CorreiosAuthClient, 'get_token')
    def test_api_timeout_falls_back(self, mock_get_token):
        self.tenant.correios_usuario = 'usr'
        self.tenant.correios_codigo_acesso = 'pass123'
        self.tenant.save()

        mock_get_token.side_effect = Exception('timeout')

        options, official = _get_correios_options(self.tenant, '20040000', 500)
        self.assertFalse(official)

    @patch('app.apps.freight.correios_cws.requests.post')
    @patch('app.apps.freight.correios_cws.cache.get')
    @patch('app.apps.freight.correios_cws.cache.set')
    @patch('app.apps.freight.correios_cws.datetime')
    def test_token_cache_avoids_second_auth(self, mock_dt, mock_cache_set, mock_cache_get, mock_requests):
        self.tenant.correios_usuario = 'usr'
        self.tenant.correios_codigo_acesso = 'pass123'
        self.tenant.save()

        mock_cache_get.return_value = None

        from datetime import datetime, timezone
        mock_dt.now.return_value = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)

        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {'token': 'jwt', 'expiraEm': '2026-07-01T18:00:00Z'}
        mock_requests.return_value = mock_resp

        client = CorreiosAuthClient()
        token1 = client.get_token(self.tenant)
        self.assertIsNotNone(token1)
        self.assertEqual(mock_requests.call_count, 1)

        mock_cache_get.return_value = {'token': 'jwt', 'expiraEm': '2026-07-01T18:00:00Z'}
        token2 = client.get_token(self.tenant)
        self.assertIsNotNone(token2)
        self.assertEqual(mock_requests.call_count, 1)

    @patch('app.apps.freight.correios_cws.requests.post')
    @patch('app.apps.freight.correios_cws.cache.get')
    @patch('app.apps.freight.correios_cws.cache.set')
    def test_token_cache_stores_dr_from_cartao_postagem(self, mock_cache_set, mock_cache_get, mock_requests):
        self.tenant.correios_usuario = 'usr'
        self.tenant.correios_codigo_acesso = 'pass123'
        self.tenant.correios_cartao = '0076666666'
        self.tenant.save()

        mock_cache_get.return_value = None
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {
            'token': 'jwt',
            'cartaoPostagem': {'dr': 72},
        }
        mock_requests.return_value = mock_resp

        token = CorreiosAuthClient().get_token(self.tenant)

        self.assertEqual(token['_resolved_dr'], '72')
        cached_token = mock_cache_set.call_args.args[1]
        self.assertEqual(cached_token['_resolved_dr'], '72')


class FreightTestCwsViewTest(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='CWS Test', slug='cws-test2', is_active=True,
            correios_usuario='usr', correios_codigo_acesso='pass',
        )
        self.admin = User.objects.create_user(
            username='admin1', password='pass',
            role=User.Role.ADMIN, tenant=self.tenant,
        )
        self.seller = User.objects.create_user(
            username='seller1', password='pass',
            role=User.Role.SELLER, tenant=self.tenant,
        )
        self.url = reverse('freight_test_cws')

    def _post(self, user):
        self.client.login(username=user.username, password='pass')
        return self.client.post(self.url, content_type='application/json')

    @patch.object(CorreiosAuthClient, 'get_token')
    def test_seller_403(self, mock_get_token):
        mock_get_token.return_value = {'token': 'jwt'}
        resp = self._post(self.seller)
        self.assertEqual(resp.status_code, 403)

    @patch.object(CorreiosAuthClient, 'get_token')
    @patch('app.apps.freight.correios_cws.requests.post')
    def test_admin_with_valid_creds_ok(self, mock_requests, mock_get_token):
        mock_get_token.return_value = {'token': 'jwt', 'expiraEm': '2099-01-01T00:00:00Z'}
        mock_price = MagicMock(status_code=200)
        mock_price.json.return_value = [{'coProduto': '03220', 'pcFinal': '28,90'}]
        mock_prazo = MagicMock(status_code=200)
        mock_prazo.json.return_value = [{'coProduto': '03220', 'prazoEntrega': '2'}]
        mock_requests.side_effect = [mock_price, mock_prazo]
        resp = self._post(self.admin)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['ok'])

    @patch.object(CorreiosAuthClient, 'get_token')
    def test_admin_with_invalid_creds_returns_error(self, mock_get_token):
        mock_get_token.return_value = None
        resp = self._post(self.admin)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data['ok'])
        self.assertIn('Credenciais invalidas', data['error'])


class GestorConfigCorreiosCwsTest(TestCase):

    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='Gestor CWS',
            slug='gestor-cws',
            is_active=True,
            store_cep='01001-000',
            correios_usuario='usr',
            correios_codigo_acesso='pass',
        )
        self.admin = User.objects.create_user(
            username='admin-cws', password='pass',
            role=User.Role.ADMIN, tenant=self.tenant,
        )
        self.url = reverse('dashboard:gestor_configuracoes')

    def _post_test(self):
        self.client.login(username=self.admin.username, password='pass')
        return self.client.post(self.url, data={
            'test_cws': '1',
            'store_cep': '01001-000',
            'correios_usuario': 'usr',
            'correios_codigo_acesso': 'pass',
            'pagarme_api_key': '',
            'whatsapp_instance_id': '',
            'link_expires_in': '1200',
            'ranking_visible_to_sellers': '0',
            'accountant_email': '',
            'accountant_auto_send': '0',
        })

    @patch.object(CorreiosAuthClient, 'get_token')
    @patch('app.apps.freight.correios_cws.requests.post')
    def test_connection_test_success_message_includes_quote_value(self, mock_requests, mock_get_token):
        mock_get_token.return_value = {'token': 'jwt-fake'}
        mock_price = MagicMock(status_code=200)
        mock_price.json.return_value = [{'coProduto': '03220', 'pcFinal': '28,90'}]
        mock_prazo = MagicMock(status_code=200)
        mock_prazo.json.return_value = [{'coProduto': '03220', 'prazoEntrega': '2'}]
        mock_requests.side_effect = [mock_price, mock_prazo]

        response = self._post_test()

        self.assertEqual(response.status_code, 302)
        messages = [str(m) for m in list(response.wsgi_request._messages)]
        self.assertTrue(any('Correios conectado' in m and 'SEDEX R$ 28,90' in m for m in messages))

    @patch.object(CorreiosAuthClient, 'get_token')
    @patch('app.apps.freight.correios_cws.requests.post')
    def test_connection_test_warning_includes_quote_error(self, mock_requests, mock_get_token):
        mock_get_token.return_value = {'token': 'jwt-fake'}
        mock_price = MagicMock(status_code=200)
        mock_price.json.return_value = [{'coProduto': '03220', 'pcFinal': '0,00', 'msgErro': 'Servico indisponivel'}]
        mock_prazo = MagicMock(status_code=200)
        mock_prazo.json.return_value = []
        mock_requests.side_effect = [mock_price, mock_prazo]

        response = self._post_test()

        self.assertEqual(response.status_code, 302)
        messages = [str(m) for m in list(response.wsgi_request._messages)]
        self.assertTrue(any('Token OK, mas a cotação falhou: Servico indisponivel' in m for m in messages))
