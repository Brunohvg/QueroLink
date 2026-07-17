from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from app.apps.accounts.models import Tenant, User
from app.apps.receivables.models import Boleto, IntegrationOutbox
from app.apps.receivables.tasks import (
    send_boleto_due_reminders,
    reprocess_stuck_outbox_events,
    report_stuck_outbox_metrics,
)
from app.apps.sellers.models import Seller


class OutboxConsumerTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='Outbox Tenant',
            plan='PRO',
            receivables_enabled=True,
        )
        self.manager = User.objects.create_user(
            username='outbox-manager',
            tenant=self.tenant,
            role=User.Role.MANAGER,
        )
        seller_user = User.objects.create_user(
            username='outbox-seller',
            tenant=self.tenant,
            role=User.Role.SELLER,
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant,
            user=seller_user,
            name='Seller Outbox',
            phone='11999999999',
        )
        today = timezone.localdate()

        self.pending_boleto = Boleto.objects.create(
            tenant=self.tenant,
            seller=self.seller,
            created_by=self.manager,
            payer_name='Payer',
            payer_document='52998224725',
            payer_document_type=Boleto.DocumentType.CPF,
            payer_email='p@test.com',
            payer_phone='11977777777',
            payer_zip_code='01310100',
            payer_street='Rua',
            payer_number='1',
            payer_neighborhood='Centro',
            payer_city='SP',
            payer_state='SP',
            amount_cents=50000,
            due_date=today + timezone.timedelta(days=1),
            status=Boleto.Status.PENDENTE,
            idempotency_key='outbox-test-1',
        )
        self.overdue_boleto = Boleto.objects.create(
            tenant=self.tenant,
            seller=self.seller,
            created_by=self.manager,
            payer_name='Overdue',
            payer_document='52998224725',
            payer_document_type=Boleto.DocumentType.CPF,
            payer_email='o@test.com',
            payer_phone='11966666666',
            payer_zip_code='01310100',
            payer_street='Rua',
            payer_number='2',
            payer_neighborhood='Centro',
            payer_city='SP',
            payer_state='SP',
            amount_cents=30000,
            due_date=today - timezone.timedelta(days=1),
            status=Boleto.Status.PENDENTE,
            idempotency_key='outbox-test-2',
        )

    def test_send_due_reminders_creates_delivery(self):
        from app.apps.receivables.models import ReceivableNotificationDelivery

        result = send_boleto_due_reminders()
        self.assertGreater(result, 0)
        self.assertTrue(
            ReceivableNotificationDelivery.objects.filter(
                tenant=self.tenant,
            ).exists()
        )

    def test_reprocess_stuck_outbox_events_processes_none_when_empty(self):
        result = reprocess_stuck_outbox_events()
        self.assertEqual(result, 0)

    def test_reprocess_stuck_outbox_events_resets_failed_events(self):
        old = timezone.now() - timezone.timedelta(hours=3)
        event = IntegrationOutbox.objects.create(
            tenant=self.tenant,
            aggregate_type='boleto',
            aggregate_uuid=self.pending_boleto.uuid,
            event_type='boleto.test',
            event_key=f'test-old:{self.pending_boleto.uuid}',
            status=IntegrationOutbox.Status.FAILED,
        )

        IntegrationOutbox.objects.filter(pk=event.pk).update(
            updated_at=old,
            available_at=old,
        )
        event.refresh_from_db()

        result = reprocess_stuck_outbox_events()
        self.assertEqual(result, 1)
        event.refresh_from_db()
        self.assertEqual(event.status, IntegrationOutbox.Status.PENDING)

    def test_report_stuck_outbox_metrics_returns_counts(self):
        IntegrationOutbox.objects.create(
            tenant=self.tenant,
            aggregate_type='boleto',
            aggregate_uuid=self.pending_boleto.uuid,
            event_type='boleto.test',
            event_key=f'test-metric:{self.pending_boleto.uuid}',
            status=IntegrationOutbox.Status.FAILED,
        )
        result = report_stuck_outbox_metrics()
        self.assertIn('failed', result)
        self.assertGreaterEqual(result['failed'], 1)
