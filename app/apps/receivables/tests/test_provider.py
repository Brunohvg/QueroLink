from datetime import date
from unittest.mock import Mock, patch
from uuid import uuid4

import requests
from django.test import SimpleTestCase, override_settings

from app.apps.receivables.providers import (
    ProviderDefinitiveError,
    ProviderInconclusiveError,
    ProviderStatus,
)
from app.apps.receivables.providers.pagarme import PagarmeBoletoProvider
from app.services.gateway.pagar_me import PagarMeGateway


class PagarmeProviderTests(SimpleTestCase):
    def setUp(self):
        self.provider = PagarmeBoletoProvider()
        self.tenant = Mock(pagarme_api_key='sk_test')

    def boleto_data(self, **overrides):
        data = {
            'uuid': uuid4(),
            'amount_cents': 15000,
            'due_date': date(2026, 8, 10),
            'instructions': 'Nao cobrar multa ou juros.',
            'payer_name': 'Maria da Silva',
            'payer_email': 'maria@example.com',
            'payer_document': '52998224725',
            'payer_document_type': 'CPF',
            'payer_phone': '11999999999',
            'payer_zip_code': '01310100',
            'payer_street': 'Avenida Paulista',
            'payer_number': '1000',
            'payer_complement': '',
            'payer_neighborhood': 'Bela Vista',
            'payer_city': 'Sao Paulo',
            'payer_state': 'SP',
        }
        data.update(overrides)
        return data

    @patch('app.apps.receivables.providers.pagarme.PagarMeGateway')
    def test_create_builds_metadata_and_uses_public_gateway_method(self, gateway_cls):
        gateway = gateway_cls.return_value
        gateway.create_boleto_order.return_value = {
            'id': 'or_123',
            'status': 'pending',
            'charges': [{
                'id': 'ch_123',
                'status': 'pending',
                'last_transaction': {'line': '123456', 'url': 'https://example.test'},
            }],
        }
        data = self.boleto_data()

        result = self.provider.create(self.tenant, data, 'stable-key')

        payload, key = gateway.create_boleto_order.call_args.args
        self.assertEqual(key, 'stable-key')
        self.assertEqual(payload['code'], str(data['uuid']))
        self.assertEqual(payload['metadata'], {
            'merito_boleto': '1',
            'boleto_uuid': str(data['uuid']),
            'idempotency_key': 'stable-key',
        })
        self.assertEqual(result.order_id, 'or_123')
        self.assertEqual(result.charge_id, 'ch_123')
        self.assertEqual(result.status, ProviderStatus.PENDING)

    def test_phone_with_ten_digits(self):
        self.assertEqual(self.provider._phone('1133334444'), {
            'country_code': '55', 'area_code': '11', 'number': '33334444',
        })

    def test_phone_with_eleven_digits(self):
        self.assertEqual(self.provider._phone('11999994444'), {
            'country_code': '55', 'area_code': '11', 'number': '999994444',
        })

    @override_settings(
        PAGARME_BOLETO_FINE_ENABLED=False,
        PAGARME_BOLETO_INTEREST_ENABLED=False,
    )
    def test_fine_and_interest_are_disabled_by_default(self):
        payload = self.provider._create_payload(self.boleto_data(), 'key')
        boleto_options = payload['payments'][0]['boleto']

        self.assertNotIn('fine', boleto_options)
        self.assertNotIn('interest', boleto_options)

    @patch('app.apps.receivables.providers.pagarme.PagarMeGateway')
    def test_response_without_ids_is_definitive_error(self, gateway_cls):
        gateway_cls.return_value.create_boleto_order.return_value = {
            'status': 'pending', 'charges': [],
        }

        with self.assertRaises(ProviderDefinitiveError):
            self.provider.create(self.tenant, self.boleto_data(), 'key')

    @patch('app.apps.receivables.providers.pagarme.PagarMeGateway')
    def test_timeout_after_post_is_inconclusive(self, gateway_cls):
        from app.services.gateway.pagar_me import PagarMeTransientError

        gateway_cls.return_value.create_boleto_order.side_effect = (
            PagarMeTransientError('timeout')
        )

        with self.assertRaises(ProviderInconclusiveError):
            self.provider.create(self.tenant, self.boleto_data(), 'key')


class PagarMeGatewayCompatibilityTests(SimpleTestCase):
    @patch('app.services.gateway.pagar_me.requests.request')
    def test_boleto_order_uses_idempotency_header(self, request):
        response = Mock(status_code=200)
        response.raise_for_status.return_value = None
        response.json.return_value = {'id': 'or_1'}
        request.return_value = response
        gateway = PagarMeGateway(api_key='sk_test')

        gateway.create_boleto_order({'code': 'abc'}, 'stable-key')

        headers = request.call_args.kwargs['headers']
        self.assertEqual(headers['Idempotency-Key'], 'stable-key')
        self.assertIn('authorization', headers)

    @patch('app.services.gateway.pagar_me.requests.request')
    def test_create_payment_link_keeps_existing_payload_and_headers(self, request):
        response = Mock(status_code=200)
        response.raise_for_status.return_value = None
        response.json.return_value = {'id': 'link_1'}
        request.return_value = response
        gateway = PagarMeGateway(api_key='sk_test')

        result = gateway.create_payment_link(
            10000, 3, 'Link Teste', 1, order_code='order-1', pix_enabled=True,
        )

        self.assertEqual(result, {'id': 'link_1'})
        payload = request.call_args.kwargs['json']
        headers = request.call_args.kwargs['headers']
        self.assertEqual(payload['order_code'], 'order-1')
        self.assertEqual(
            payload['payment_settings']['accepted_payment_methods'],
            ['credit_card', 'pix'],
        )
        self.assertNotIn('Idempotency-Key', headers)

    @patch('app.services.gateway.pagar_me.requests.request')
    def test_http_422_remains_definitive(self, request):
        response = Mock(status_code=422)
        response.raise_for_status.side_effect = requests.HTTPError(response=response)
        request.return_value = response
        gateway = PagarMeGateway(api_key='sk_test')

        from app.services.gateway.pagar_me import PagarMeError

        with self.assertRaises(PagarMeError):
            gateway.create_boleto_order({}, 'key')
