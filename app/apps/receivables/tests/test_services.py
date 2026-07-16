from unittest.mock import Mock, patch

from django.test import TestCase

from ..providers import ProviderResult
from ..services import create_boleto
from .helpers import boleto_data, make_seller, make_tenant


class BoletoServiceTests(TestCase):
    def setUp(self):
        self.tenant = make_tenant()
        self.user, self.seller = make_seller(self.tenant)

    @patch('app.apps.receivables.services.get_provider')
    @patch('app.apps.receivables.tasks.send_boleto_email.delay')
    def test_create_persists_provider_result_after_validation(self, email_mock, provider_mock):
        provider = Mock()
        provider.create.return_value = ProviderResult(
            gateway='PAGARME', order_id='or_123', charge_id='ch_123',
            barcode='123456', url='https://example.com/boleto.pdf',
        )
        provider_mock.return_value = provider
        with self.captureOnCommitCallbacks(execute=True):
            boleto = create_boleto(
                self.tenant, self.seller, self.user, boleto_data(),
            )
        self.assertEqual(boleto.gateway_order_id, 'or_123')
        self.assertEqual(boleto.gateway_charge_id, 'ch_123')
        self.assertEqual(boleto.barcode, '123456')
        provider.create.assert_called_once()
        email_mock.assert_called_once_with(str(boleto.uuid), 'created')
