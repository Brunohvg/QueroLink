from datetime import timedelta
import shutil
import tempfile
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from app.apps.accounts.models import Tenant, User
from app.apps.audit.models import AuditLog
from app.apps.receivables.document_services import PDF_MAX_BYTES, XML_MAX_BYTES
from app.apps.receivables.models import Boleto
from app.apps.sellers.models import Seller


class InvoiceDocumentTests(TestCase):
    def setUp(self):
        self.media_root = tempfile.mkdtemp(prefix='receivables-documents-')
        self.settings_override = override_settings(MEDIA_ROOT=self.media_root)
        self.settings_override.enable()
        self.client = APIClient()
        self.tenant = Tenant.objects.create(company_name='Document Tenant')
        self.manager = User.objects.create_user(
            username='document-manager', tenant=self.tenant, role=User.Role.MANAGER
        )
        seller_user = User.objects.create_user(
            username='document-seller', tenant=self.tenant, role=User.Role.SELLER
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant,
            user=seller_user,
            name='Seller',
            phone='11999999999',
        )
        self.boleto = Boleto.objects.create(
            tenant=self.tenant,
            seller=self.seller,
            created_by=self.manager,
            payer_name='Maria Silva',
            payer_document='52998224725',
            payer_document_type=Boleto.DocumentType.CPF,
            payer_email='maria@example.com',
            payer_phone='11988887777',
            payer_zip_code='01310100',
            payer_street='Avenida Paulista',
            payer_number='1000',
            payer_neighborhood='Bela Vista',
            payer_city='Sao Paulo',
            payer_state='SP',
            amount_cents=15000,
            due_date=timezone.localdate() + timedelta(days=10),
            idempotency_key='invoice-document',
            status=Boleto.Status.PAGO,
        )
        self.client.force_authenticate(self.manager)

    def tearDown(self):
        self.settings_override.disable()
        shutil.rmtree(self.media_root, ignore_errors=True)

    def upload_url(self, document_type):
        return reverse(
            'receivables:invoice-upload', args=[self.boleto.uuid, document_type]
        )

    def download_url(self, document_type):
        return reverse(
            'receivables:invoice-download', args=[self.boleto.uuid, document_type]
        )

    def test_upload_valid_pdf_records_metadata_and_safe_audit(self):
        upload = SimpleUploadedFile(
            'invoice.pdf', b'%PDF-1.7\nvalid', content_type='application/pdf'
        )

        response = self.client.post(self.upload_url('pdf'), {'file': upload})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.boleto.refresh_from_db()
        self.assertTrue(self.boleto.invoice_pdf.name.startswith(
            f'receivables_private/{self.tenant.uuid}/{self.boleto.uuid}/'
        ))
        self.assertEqual(self.boleto.invoice_pdf_uploaded_by, self.manager)
        self.assertIsNotNone(self.boleto.invoice_pdf_uploaded_at)
        audit = AuditLog.objects.get(action='receivable.invoice_uploaded')
        self.assertEqual(audit.changes, {'document_type': 'pdf', 'replaced': False})
        self.assertNotIn('%PDF', str(audit.changes))

    def test_invalid_pdf_returns_400(self):
        upload = SimpleUploadedFile('invoice.pdf', b'not-a-pdf')

        response = self.client.post(self.upload_url('pdf'), {'file': upload})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.boleto.refresh_from_db()
        self.assertFalse(self.boleto.invoice_pdf)

    def test_valid_and_invalid_xml(self):
        valid = SimpleUploadedFile('invoice.xml', b'<invoice><id>1</id></invoice>')
        response = self.client.post(self.upload_url('xml'), {'file': valid})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        invalid = SimpleUploadedFile(
            'unsafe.xml', b'<!DOCTYPE x [<!ENTITY y "z">]><invoice>&y;</invoice>'
        )
        response = self.client.post(self.upload_url('xml'), {'file': invalid})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_size_limits_return_400(self):
        for document_type, size in (
            ('pdf', PDF_MAX_BYTES + 1),
            ('xml', XML_MAX_BYTES + 1),
        ):
            with self.subTest(document_type=document_type):
                upload = SimpleUploadedFile(
                    f'invoice.{document_type}', b'x' * size
                )
                response = self.client.post(
                    self.upload_url(document_type), {'file': upload}
                )
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_replacement_deletes_old_file_after_commit(self):
        first = SimpleUploadedFile('first.pdf', b'%PDF-1.7\nfirst')
        self.client.post(self.upload_url('pdf'), {'file': first})
        self.boleto.refresh_from_db()
        old_name = self.boleto.invoice_pdf.name
        storage = self.boleto.invoice_pdf.storage
        self.assertTrue(storage.exists(old_name))

        second = SimpleUploadedFile('second.pdf', b'%PDF-1.7\nsecond')
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(self.upload_url('pdf'), {'file': second})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.boleto.refresh_from_db()
        self.assertNotEqual(self.boleto.invoice_pdf.name, old_name)
        self.assertFalse(storage.exists(old_name))
        self.assertTrue(storage.exists(self.boleto.invoice_pdf.name))

    def test_old_file_delete_failure_does_not_break_response(self):
        self.client.post(
            self.upload_url('pdf'),
            {'file': SimpleUploadedFile('first.pdf', b'%PDF-1.7\nfirst')},
        )
        with patch(
            'app.apps.receivables.document_services._delete_old_file',
            side_effect=RuntimeError('delete failed'),
        ), self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                self.upload_url('pdf'),
                {'file': SimpleUploadedFile('second.pdf', b'%PDF-1.7\nsecond')},
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    @patch(
        'django.db.models.fields.files.FieldFile.save',
        side_effect=OSError('storage unavailable'),
    )
    def test_unexpected_storage_failure_returns_500(self, _save):
        response = self.client.post(
            self.upload_url('pdf'),
            {'file': SimpleUploadedFile('invoice.pdf', b'%PDF-1.7\nvalid')},
        )

        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertNotEqual(response.status_code, status.HTTP_200_OK)

    def test_private_download_returns_attachment(self):
        self.client.post(
            self.upload_url('pdf'),
            {'file': SimpleUploadedFile('invoice.pdf', b'%PDF-1.7\ndownload')},
        )

        response = self.client.get(self.download_url('pdf'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(b''.join(response.streaming_content), b'%PDF-1.7\ndownload')
        self.assertIn('attachment;', response['Content-Disposition'])
