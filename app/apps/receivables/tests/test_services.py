from datetime import timedelta
from unittest.mock import Mock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from ..providers import ProviderResult
from ..models import Boleto
from ..services import (
    BoletoServiceError, create_boleto, mark_paid,
    save_invoice_files, _validate_invoice_file,
)
from .helpers import boleto_data, make_seller, make_tenant, make_boleto


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

    @patch('app.apps.receivables.services.get_provider')
    def test_failed_provider_response_does_not_persist_boleto(self, provider_mock):
        provider_mock.return_value.create.side_effect = RuntimeError('failed')
        with self.assertRaises(BoletoServiceError):
            create_boleto(self.tenant, self.seller, self.user, boleto_data())
        self.assertFalse(Boleto.objects.exists())


class MarkPaidTests(TestCase):
    def setUp(self):
        self.tenant = make_tenant()
        self.user, self.seller = make_seller(self.tenant)
        self.boleto = make_boleto(
            self.tenant, self.seller, self.user,
        )

    @patch('app.apps.receivables.tasks.notify_boleto_paid.delay')
    def test_mark_paid_creates_customer_snapshot(self, notify_mock):
        with self.captureOnCommitCallbacks(execute=True):
            locked, created = mark_paid(self.boleto, 15000, timezone.now())
        self.boleto.refresh_from_db()
        self.assertTrue(created)
        self.assertEqual(self.boleto.status, Boleto.Status.PAGO)
        self.assertIsNotNone(self.boleto.customer_snapshot)
        snapshot = self.boleto.customer_snapshot
        self.assertEqual(snapshot['name'], self.boleto.payer_name)
        self.assertEqual(snapshot['document'], self.boleto.payer_document)
        self.assertIn('captured_at', snapshot)

    @patch('app.apps.receivables.tasks.notify_boleto_paid.delay')
    def test_mark_paid_is_idempotent(self, notify_mock):
        self.boleto.status = Boleto.Status.PAGO
        self.boleto.save()
        locked, created = mark_paid(self.boleto, 99999, timezone.now())
        self.assertFalse(created)
        self.assertEqual(self.boleto.paid_amount_cents, 15000)

class InvoiceValidationTests(TestCase):
    def test_rejects_empty_file(self):
        upload = SimpleUploadedFile('nota.pdf', b'')
        with self.assertRaises(BoletoServiceError) as ctx:
            _validate_invoice_file(upload, 'pdf')
        self.assertIn('vazio', str(ctx.exception))

    def test_rejects_file_exceeding_size_limit(self):
        content = b'%PDF-' + b'x' * (10 * 1024 * 1024)
        upload = SimpleUploadedFile('nota.pdf', content, content_type='application/pdf')
        with self.assertRaises(BoletoServiceError) as ctx:
            _validate_invoice_file(upload, 'pdf')
        self.assertIn('maximo', str(ctx.exception))

    def test_rejects_invalid_pdf_magic(self):
        upload = SimpleUploadedFile('nota.pdf', b'GIF89a...', content_type='application/pdf')
        with self.assertRaises(BoletoServiceError) as ctx:
            _validate_invoice_file(upload, 'pdf')
        self.assertIn('PDF valido', str(ctx.exception))

    def test_rejects_invalid_xml_content(self):
        upload = SimpleUploadedFile('nota.xml', b'GIF89a...', content_type='application/xml')
        with self.assertRaises(BoletoServiceError) as ctx:
            _validate_invoice_file(upload, 'xml')
        self.assertIn('XML valido', str(ctx.exception))

    def test_accepts_valid_pdf(self):
        upload = SimpleUploadedFile('nota.pdf', b'%PDF-1.4 test', content_type='application/pdf')
        _validate_invoice_file(upload, 'pdf')

    def test_accepts_valid_xml(self):
        upload = SimpleUploadedFile('nota.xml', b'<?xml version="1.0"?><nfeProc/>', content_type='application/xml')
        _validate_invoice_file(upload, 'xml')

    def test_accepts_octet_stream_content_type(self):
        upload = SimpleUploadedFile('nota.pdf', b'%PDF-1.4 test', content_type='application/octet-stream')
        _validate_invoice_file(upload, 'pdf')

    def test_accepts_empty_content_type_with_valid_content(self):
        upload = SimpleUploadedFile('nota.pdf', b'%PDF-1.4 test', content_type='')
        _validate_invoice_file(upload, 'pdf')


class InvoiceUploadIntegrationTests(TestCase):
    def setUp(self):
        self.tenant = make_tenant()
        self.user, self.seller = make_seller(self.tenant)
        self.boleto = make_boleto(self.tenant, self.seller, self.user)

    def test_save_invoice_files_requires_at_least_one_file(self):
        with self.assertRaises(BoletoServiceError) as ctx:
            save_invoice_files(self.boleto, self.user, pdf=None, xml=None)
        self.assertIn('ao menos um arquivo', str(ctx.exception))

    def test_save_valid_pdf_updates_invoice_fields(self):
        pdf = SimpleUploadedFile('nota.pdf', b'%PDF-1.4 content', content_type='application/pdf')
        boleto = save_invoice_files(self.boleto, self.user, pdf=pdf)
        self.assertTrue(bool(boleto.invoice_pdf))
        self.assertIsNotNone(boleto.invoice_uploaded_at)
        self.assertEqual(boleto.invoice_uploaded_by, self.user)

    def test_save_xml_persists_file(self):
        xml_content = b'<?xml version="1.0"?><root><test>data</test></root>'
        xml = SimpleUploadedFile('nota.xml', xml_content, content_type='application/xml')
        boleto = save_invoice_files(self.boleto, self.user, xml=xml)
        self.assertTrue(bool(boleto.invoice_xml))
        self.assertIsNotNone(boleto.invoice_uploaded_at)
        self.assertEqual(boleto.invoice_uploaded_by, self.user)
