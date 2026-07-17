from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from app.apps.accounts.models import Tenant, User
from app.apps.customers.management.commands.backfill_customer_ledger import Command
from app.apps.customers.models import Customer, CustomerActivity
from app.apps.orders.models import Order, PaymentLink
from app.apps.receivables.models import Boleto
from app.apps.sellers.models import Seller


class CustomerBackfillCommandTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(company_name='Backfill Tenant')
        self.manager = User.objects.create_user(
            username='backfill-manager', tenant=self.tenant, role=User.Role.MANAGER
        )
        seller_user = User.objects.create_user(
            username='backfill-seller', tenant=self.tenant, role=User.Role.SELLER
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant,
            user=seller_user,
            name='Seller',
            phone='11999999999',
        )
        self.order = Order.objects.create(
            tenant=self.tenant,
            seller=self.seller,
            customer_name='Order Customer',
            customer_phone='11977776666',
            total_amount=12500,
            status=Order.Status.COMPLETED,
        )
        PaymentLink.objects.create(order=self.order, short_code='backfill-link')
        self.boleto = Boleto.objects.create(
            tenant=self.tenant,
            seller=self.seller,
            created_by=self.manager,
            payer_name='Boleto Customer',
            payer_document='52998224725',
            payer_document_type=Boleto.DocumentType.CPF,
            payer_email='boleto@example.com',
            payer_phone='11988887777',
            payer_zip_code='01310100',
            payer_street='Avenida Paulista',
            payer_number='1000',
            payer_neighborhood='Bela Vista',
            payer_city='Sao Paulo',
            payer_state='SP',
            amount_cents=15000,
            due_date=timezone.localdate() + timedelta(days=10),
            idempotency_key='backfill-boleto',
            status=Boleto.Status.PAGO,
            paid_amount_cents=15000,
            paid_at=timezone.now(),
        )

    def run_command(self, *args):
        stdout = StringIO()
        stderr = StringIO()
        call_command('backfill_customer_ledger', *args, stdout=stdout, stderr=stderr)
        return stdout.getvalue(), stderr.getvalue()

    def test_dry_run_does_not_write(self):
        stdout, _ = self.run_command(
            '--tenant', str(self.tenant.uuid), '--dry-run', '--batch-size', '1'
        )

        self.assertEqual(Customer.objects.count(), 0)
        self.assertEqual(CustomerActivity.objects.count(), 0)
        self.assertIn('processed=2', stdout)
        self.assertIn('created=2', stdout)

    def test_second_execution_does_not_duplicate_activities(self):
        self.run_command('--tenant', str(self.tenant.uuid))
        self.run_command('--tenant', str(self.tenant.uuid))

        self.assertEqual(CustomerActivity.objects.count(), 2)
        self.assertEqual(
            CustomerActivity.objects.filter(source='PAYMENT_LINK').count(), 1
        )
        self.assertEqual(
            CustomerActivity.objects.filter(source='BOLETO').count(), 1
        )

    def test_cursor_resumes_after_previous_uuid_for_each_source(self):
        self.run_command(
            '--tenant', str(self.tenant.uuid),
            '--cursor', f'orders:{self.order.uuid}',
        )

        self.assertFalse(CustomerActivity.objects.filter(
            source='PAYMENT_LINK', source_uuid=self.order.uuid
        ).exists())

    def test_error_in_one_tenant_does_not_stop_all_tenants(self):
        other = Tenant.objects.create(company_name='Backfill Other Tenant')
        original = Command._backfill_tenant

        def fail_one(command, tenant, **kwargs):
            if tenant.pk == self.tenant.pk:
                raise RuntimeError('simulated tenant failure')
            return original(command, tenant, **kwargs)

        with patch.object(Command, '_backfill_tenant', new=fail_one):
            stdout, stderr = self.run_command('--all-tenants')

        self.assertIn(f'Falha no tenant {self.tenant.uuid}: RuntimeError', stderr)
        self.assertIn('errors=1', stdout)
        self.assertIn('tenants=2', stdout)
        self.assertFalse(CustomerActivity.objects.filter(tenant=other).exists())
