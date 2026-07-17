from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from app.apps.accounts.models import Tenant, User
from app.apps.receivables.models import (
    Boleto, IntegrationOutbox, ReceivableNotificationDelivery,
)
from app.apps.receivables.notification_services import (
    _deliver_boleto_created,
    _deliver_boleto_paid,
    deliver_outbox_event,
    _delivery_key,
)
from app.apps.sellers.models import Seller


class DeliveryTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='Delivery Tenant',
            plan='PRO',
            receivables_enabled=True,
        )
        self.manager = User.objects.create_user(
            username='del-manager',
            tenant=self.tenant,
            role=User.Role.MANAGER,
            email='manager@del.com',
        )
        seller_user = User.objects.create_user(
            username='del-seller',
            tenant=self.tenant,
            role=User.Role.SELLER,
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant,
            user=seller_user,
            name='Seller Del',
            phone='11999999999',
        )
        self.boleto = Boleto.objects.create(
            tenant=self.tenant,
            seller=self.seller,
            created_by=self.manager,
            payer_name='Payer',
            payer_document='52998224725',
            payer_document_type=Boleto.DocumentType.CPF,
            payer_email='payer@del.com',
            payer_phone='11977777777',
            payer_zip_code='01310100',
            payer_street='Rua',
            payer_number='1',
            payer_neighborhood='Centro',
            payer_city='SP',
            payer_state='SP',
            amount_cents=50000,
            due_date=timezone.localdate() + timezone.timedelta(days=30),
            status=Boleto.Status.PENDENTE,
            idempotency_key='del-test-1',
            provider_barcode='12345678901234567890',
            provider_url='https://boleto.test/123',
        )

    def _make_outbox_event(self, event_type='boleto.created'):
        return IntegrationOutbox.objects.create(
            tenant=self.tenant,
            aggregate_type='boleto',
            aggregate_uuid=self.boleto.uuid,
            event_type=event_type,
            event_key=f'test:{event_type}:{self.boleto.uuid}',
            payload={'boleto_uuid': str(self.boleto.uuid)},
        )

    @patch('django.core.mail.send_mail')
    def test_deliver_created_sends_email_to_payer(self, mock_send):
        event = self._make_outbox_event()
        _deliver_boleto_created(event)
        deliveries = ReceivableNotificationDelivery.objects.filter(
            outbox_event=event,
        )
        self.assertGreater(deliveries.count(), 0)
        email_delivery = deliveries.filter(channel='email').first()
        self.assertIsNotNone(email_delivery)
        self.assertEqual(
            email_delivery.status,
            ReceivableNotificationDelivery.Status.SENT,
        )

    @patch('django.core.mail.send_mail')
    def test_deliver_created_without_email_skips(self, mock_send):
        self.boleto.payer_email = ''
        self.boleto.save(update_fields=['payer_email'])
        event = self._make_outbox_event()
        _deliver_boleto_created(event)
        deliveries = ReceivableNotificationDelivery.objects.filter(
            outbox_event=event, channel='email',
        )
        self.assertEqual(deliveries.count(), 1)
        self.assertEqual(
            deliveries.first().status,
            ReceivableNotificationDelivery.Status.SKIPPED,
        )

    @patch('django.core.mail.send_mail')
    def test_deliver_is_idempotent(self, mock_send):
        event = self._make_outbox_event()
        _deliver_boleto_created(event)
        first_count = ReceivableNotificationDelivery.objects.filter(
            outbox_event=event,
        ).count()
        _deliver_boleto_created(event)
        second_count = ReceivableNotificationDelivery.objects.filter(
            outbox_event=event,
        ).count()
        self.assertEqual(first_count, second_count)

    @patch('django.core.mail.send_mail')
    def test_deliver_outbox_event_routes_correctly(self, mock_send):
        event = self._make_outbox_event('boleto.created')
        result = deliver_outbox_event(event)
        self.assertTrue(result)
        self.assertTrue(
            ReceivableNotificationDelivery.objects.filter(
                outbox_event=event,
            ).exists()
        )

    def test_deliver_unknown_event_returns_true(self):
        event = self._make_outbox_event('boleto.unknown')
        result = deliver_outbox_event(event)
        self.assertTrue(result)

    def test_delivery_key_is_deterministic(self):
        k1 = _delivery_key('uuid', 'created', 'email', 'a@b.com')
        k2 = _delivery_key('uuid', 'created', 'email', 'a@b.com')
        self.assertEqual(k1, k2)
        k3 = _delivery_key('uuid', 'created', 'email', 'c@d.com')
        self.assertNotEqual(k1, k3)

    @patch('django.core.mail.send_mail')
    def test_deliver_paid_sends_email(self, mock_send):
        self.boleto.paid_amount_cents = 50000
        self.boleto.paid_at = timezone.now()
        self.boleto.save(update_fields=['paid_amount_cents', 'paid_at'])
        event = self._make_outbox_event('boleto.paid')
        _deliver_boleto_paid(event)
        deliveries = ReceivableNotificationDelivery.objects.filter(
            outbox_event=event, channel='email',
        )
        self.assertEqual(deliveries.count(), 1)
        self.assertEqual(
            deliveries.first().status,
            ReceivableNotificationDelivery.Status.SENT,
        )
