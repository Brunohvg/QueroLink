from datetime import timedelta
from unittest.mock import patch

from django.test import TransactionTestCase
from django.utils import timezone

from app.apps.accounts.models import Tenant, User
from app.apps.customers.models import CustomerActivity
from app.apps.receivables.models import Boleto, IntegrationOutbox
from app.apps.receivables.services import claim_outbox_events, mark_paid
from app.apps.receivables.tasks import (
    OUTBOX_HANDLERS,
    _mark_boleto_awaiting_allocation,
    process_outbox_event,
)
from app.apps.sellers.models import Seller


class CustomerOutboxTests(TransactionTestCase):
    def setUp(self):
        self.previous_handlers = OUTBOX_HANDLERS.copy()
        OUTBOX_HANDLERS.clear()
        OUTBOX_HANDLERS['boleto.paid'] = _mark_boleto_awaiting_allocation
        self.tenant = Tenant.objects.create(company_name='CRM Outbox Tenant')
        self.manager = User.objects.create_user(
            username='crm-manager', tenant=self.tenant, role=User.Role.MANAGER
        )
        seller_user = User.objects.create_user(
            username='crm-seller', tenant=self.tenant, role=User.Role.SELLER
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
            idempotency_key='crm-outbox-key',
            status=Boleto.Status.PENDENTE,
        )

    def tearDown(self):
        OUTBOX_HANDLERS.clear()
        OUTBOX_HANDLERS.update(self.previous_handlers)

    def test_outbox_payload_contains_no_pii(self):
        mark_paid(self.boleto, 15000, timezone.now())
        event = IntegrationOutbox.objects.get(event_type='boleto.paid')

        self.assertEqual(event.payload, {'boleto_uuid': str(self.boleto.uuid)})

    @patch(
        'app.apps.customers.services.project_outbox_event',
        side_effect=RuntimeError('crm unavailable'),
    )
    def test_customer_failure_does_not_change_payment(self, _project):
        mark_paid(self.boleto, 15000, timezone.now())
        event = claim_outbox_events(1)[0]

        with self.assertRaises(RuntimeError):
            process_outbox_event(str(event.uuid))

        self.boleto.refresh_from_db()
        event.refresh_from_db()
        self.assertEqual(self.boleto.status, Boleto.Status.PAGO)
        self.assertEqual(event.status, IntegrationOutbox.Status.FAILED)
        self.assertFalse(CustomerActivity.objects.exists())

    @patch(
        'app.apps.customers.services.project_outbox_event',
        side_effect=[RuntimeError('crm unavailable'), None],
    )
    def test_later_retry_completes_projection(self, project):
        mark_paid(self.boleto, 15000, timezone.now())
        event = claim_outbox_events(1)[0]
        with self.assertRaises(RuntimeError):
            process_outbox_event(str(event.uuid))

        event.refresh_from_db()
        event.available_at = timezone.now()
        event.save(update_fields=['available_at'])
        event = claim_outbox_events(1)[0]
        project.side_effect = None
        project.return_value = __import__(
            'app.apps.customers.services', fromlist=['project_outbox_event']
        ).project_boleto(self.boleto)
        process_outbox_event(str(event.uuid))

        event.refresh_from_db()
        self.assertEqual(event.status, IntegrationOutbox.Status.PROCESSED)
        self.assertEqual(CustomerActivity.objects.count(), 1)
