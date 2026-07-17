from datetime import timedelta
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from app.apps.accounts.models import Tenant, User
from app.apps.receivables.models import Boleto
from app.apps.sellers.models import Seller


class InvoiceDocumentPermissionTests(TestCase):
    def setUp(self):
        self.media_root = tempfile.mkdtemp(prefix='receivables-permissions-')
        self.settings_override = override_settings(MEDIA_ROOT=self.media_root)
        self.settings_override.enable()
        self.client = APIClient()
        self.tenant = Tenant.objects.create(company_name='Permission Tenant')
        self.other_tenant = Tenant.objects.create(company_name='Other Tenant')
        self.manager = User.objects.create_user(
            username='permission-manager', tenant=self.tenant, role=User.Role.MANAGER
        )
        self.admin = User.objects.create_user(
            username='permission-admin', tenant=self.tenant, role=User.Role.ADMIN
        )
        seller_user = User.objects.create_user(
            username='permission-seller', tenant=self.tenant, role=User.Role.SELLER
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant,
            user=seller_user,
            name='Seller',
            phone='11999999999',
        )
        self.boleto = self.make_boleto(self.tenant, self.seller, self.manager)

        other_manager = User.objects.create_user(
            username='other-manager', tenant=self.other_tenant, role=User.Role.MANAGER
        )
        other_seller_user = User.objects.create_user(
            username='other-seller', tenant=self.other_tenant, role=User.Role.SELLER
        )
        other_seller = Seller.objects.create(
            tenant=self.other_tenant,
            user=other_seller_user,
            name='Other Seller',
            phone='11888888888',
        )
        self.other_boleto = self.make_boleto(
            self.other_tenant, other_seller, other_manager
        )

    def tearDown(self):
        self.settings_override.disable()
        shutil.rmtree(self.media_root, ignore_errors=True)

    @staticmethod
    def make_boleto(tenant, seller, manager):
        return Boleto.objects.create(
            tenant=tenant,
            seller=seller,
            created_by=manager,
            payer_name='Maria Silva',
            payer_document='52998224725',
            payer_document_type=Boleto.DocumentType.CPF,
            payer_email='',
            payer_phone='11988887777',
            payer_zip_code='01310100',
            payer_street='Avenida Paulista',
            payer_number='1000',
            payer_neighborhood='Bela Vista',
            payer_city='Sao Paulo',
            payer_state='SP',
            amount_cents=15000,
            due_date=timezone.localdate() + timedelta(days=10),
            idempotency_key=f'permission-{tenant.uuid}',
        )

    @staticmethod
    def url(boleto, action='upload'):
        return reverse(
            f'receivables:invoice-{action}', args=[boleto.uuid, 'pdf']
        )

    def test_admin_and_manager_can_upload(self):
        for user in (self.admin, self.manager):
            with self.subTest(role=user.role):
                self.client.force_authenticate(user)
                response = self.client.post(
                    self.url(self.boleto),
                    {'file': SimpleUploadedFile('invoice.pdf', b'%PDF-1.7\nvalid')},
                )
                self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_seller_cannot_upload_or_download(self):
        self.client.force_authenticate(self.seller.user)

        upload_response = self.client.post(
            self.url(self.boleto),
            {'file': SimpleUploadedFile('invoice.pdf', b'%PDF-1.7\nvalid')},
        )
        download_response = self.client.get(self.url(self.boleto, 'download'))

        self.assertEqual(upload_response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(download_response.status_code, status.HTTP_403_FORBIDDEN)

    def test_other_tenant_cannot_upload_or_download(self):
        self.client.force_authenticate(self.manager)

        upload_response = self.client.post(
            self.url(self.other_boleto),
            {'file': SimpleUploadedFile('invoice.pdf', b'%PDF-1.7\nvalid')},
        )
        download_response = self.client.get(self.url(self.other_boleto, 'download'))

        self.assertEqual(upload_response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(download_response.status_code, status.HTTP_404_NOT_FOUND)

    @override_settings(DEBUG=True)
    def test_private_media_path_is_not_publicly_served_in_debug(self):
        self.client.force_authenticate(self.manager)
        self.client.post(
            self.url(self.boleto),
            {'file': SimpleUploadedFile('invoice.pdf', b'%PDF-1.7\nvalid')},
        )
        self.boleto.refresh_from_db()

        response = self.client.get(f'/media/{self.boleto.invoice_pdf.name}')

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
