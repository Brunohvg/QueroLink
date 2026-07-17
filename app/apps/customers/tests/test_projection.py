from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from app.apps.accounts.models import Tenant, User
from app.apps.customers.models import CustomerActivity
from app.apps.customers.services import project_boleto
from app.apps.receivables.models import Boleto
from app.apps.sellers.models import Seller


class CustomerProjectionTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(company_name='Projection Tenant')
        self.manager = User.objects.create_user(
            username='projection-manager', tenant=self.tenant, role=User.Role.MANAGER
        )
        seller_user = User.objects.create_user(
            username='projection-seller', tenant=self.tenant, role=User.Role.SELLER
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
            idempotency_key='projection-key',
            status=Boleto.Status.PAGO,
            paid_amount_cents=15000,
            paid_at=timezone.now(),
        )

    def test_activity_projection_is_idempotent(self):
        first = project_boleto(self.boleto)
        self.boleto.status = Boleto.Status.ESTORNADO
        self.boleto.refunded_at = timezone.now()
        self.boleto.save(update_fields=['status', 'refunded_at', 'updated_at'])
        second = project_boleto(self.boleto)

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(CustomerActivity.objects.count(), 1)
        second.refresh_from_db()
        self.assertEqual(second.status, Boleto.Status.ESTORNADO)
        self.assertEqual(second.tenant, self.tenant)
