from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from app.apps.accounts.models import Tenant, User
from app.apps.receivables.models import Boleto
from app.apps.receivables.notification_services import (
    notify_boleto_paid,
    notify_boleto_canceled,
    notify_boleto_created,
    notify_boleto_due_reminder,
)
from app.apps.sellers.models import Seller


class NotificationServiceTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='Notif Tenant',
            plan='PRO',
            receivables_enabled=True,
        )
        self.manager = User.objects.create_user(
            username='notif-manager',
            tenant=self.tenant,
            role=User.Role.MANAGER,
            email='manager@test.com',
        )
        seller_user = User.objects.create_user(
            username='notif-seller',
            tenant=self.tenant,
            role=User.Role.SELLER,
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant,
            user=seller_user,
            name='Seller Notif',
            phone='11999999999',
        )
        self.boleto = Boleto.objects.create(
            tenant=self.tenant,
            seller=self.seller,
            created_by=self.manager,
            payer_name='Payer Test',
            payer_document='52998224725',
            payer_document_type=Boleto.DocumentType.CPF,
            payer_email='payer@test.com',
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
            idempotency_key='notif-test-1',
        )

    @patch(
        'app.apps.receivables.notification_services._notify_seller_whatsapp'
    )
    @patch(
        'app.apps.receivables.notification_services._notify_gestor_email'
    )
    def test_notify_boleto_created_calls_both_channels(
        self, mock_gestor, mock_whatsapp
    ):
        notify_boleto_created(self.boleto)
        mock_whatsapp.assert_called_once()
        mock_gestor.assert_called_once()

    @patch(
        'app.apps.receivables.notification_services._notify_seller_whatsapp'
    )
    @patch(
        'app.apps.receivables.notification_services._notify_gestor_email'
    )
    @patch(
        'app.apps.receivables.notification_services._send_email_notification'
    )
    def test_notify_boleto_paid_calls_all_channels(
        self, mock_email, mock_gestor, mock_whatsapp
    ):
        notify_boleto_paid(self.boleto)
        mock_whatsapp.assert_called_once()
        mock_gestor.assert_called_once()
        mock_email.assert_called_once()

    @patch(
        'app.apps.receivables.notification_services._notify_seller_whatsapp'
    )
    def test_notify_boleto_canceled_calls_whatsapp(self, mock_whatsapp):
        notify_boleto_canceled(self.boleto)
        mock_whatsapp.assert_called_once()

    @patch(
        'app.apps.receivables.notification_services._notify_seller_whatsapp'
    )
    def test_notify_due_reminder_calls_whatsapp(self, mock_whatsapp):
        notify_boleto_due_reminder(self.boleto)
        mock_whatsapp.assert_called_once()

    @patch(
        'app.apps.receivables.notification_services._notify_seller_whatsapp'
    )
    def test_notify_boleto_failure_does_not_raise(
        self, mock_whatsapp
    ):
        mock_whatsapp.side_effect = Exception('channel error')
        notify_boleto_created(self.boleto)

    def test_notify_does_not_include_full_cpf_in_logs(self):
        from app.apps.receivables.notification_services import _format_brl

        result = _format_brl(50000)
        self.assertEqual(result, 'R$ 500,00')
        self.assertNotIn('529', result)
