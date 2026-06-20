from unittest.mock import patch, MagicMock
from django.test import TestCase
from app.apps.accounts.models import Tenant, User
from app.apps.sellers.models import Seller
from app.apps.commissions.models import CommissionPeriod, SellerCommission
from app.apps.notifications.models import Notification, MessageTemplate


class SendWhatsappNotificationTaskTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(company_name="Bibelo", cnpj="11111111111111")
        self.user = User.objects.create_user(
            username="vendedor_teste", password="pass", role=User.Role.SELLER, tenant=self.tenant
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant, name="Vendedor Teste", phone="31999999999", user=self.user,
        )

        self.template = MessageTemplate.objects.create(
            tenant=self.tenant,
            event_type=MessageTemplate.EventType.SELLER_CREDENTIALS,
            channel=MessageTemplate.Channel.WHATSAPP,
            body="Ola {{vendedor}}! Seu acesso: usuario={{usuario}}, senha={{senha}}",
        )

    @patch("app.apps.notifications.tasks.send_whatsapp_notification.delay")
    def test_create_and_send_notification_renders_template(self, mock_delay):
        from app.apps.notifications.tasks import create_and_send_notification

        notif = create_and_send_notification(
            tenant=self.tenant,
            event_type=MessageTemplate.EventType.SELLER_CREDENTIALS,
            channel=MessageTemplate.Channel.WHATSAPP,
            recipient="31999999999",
            seller=self.seller,
            context={
                "vendedor": self.seller.name,
                "usuario": self.seller.user.username,
                "senha": "abc123",
            },
        )

        self.assertIsNotNone(notif.pk)
        self.assertIn("Vendedor Teste", notif.message_body)
        self.assertIn("vendedor_teste", notif.message_body)
        self.assertIn("abc123", notif.message_body)
        mock_delay.assert_called_once()

    @patch("app.apps.notifications.tasks.send_whatsapp_notification.delay")
    def test_notify_seller_credentials(self, mock_delay):
        from app.apps.notifications.tasks import notify_seller_credentials

        notify_seller_credentials(self.seller, "senha123")

        notif = Notification.objects.filter(
            seller=self.seller,
            event_type=MessageTemplate.EventType.SELLER_CREDENTIALS,
        ).first()
        self.assertIsNotNone(notif)
        self.assertIn("Vendedor Teste", notif.message_body)
        self.assertIn("vendedor_teste", notif.message_body)
        self.assertIn("senha123", notif.message_body)
        mock_delay.assert_called_once()

    @patch("app.apps.notifications.tasks.send_whatsapp_notification.delay")
    def test_notify_commission_paid(self, mock_delay):
        period = CommissionPeriod.objects.create(
            tenant=self.tenant, month=6, year=2026,
        )
        sc = SellerCommission.objects.create(
            period=period, seller=self.seller,
            total_sold_amount=50000, commission_rate=0.05, commission_amount=2500,
        )

        from app.apps.notifications.tasks import notify_commission_paid

        notify_commission_paid(sc)

        notif = Notification.objects.filter(
            seller=self.seller,
            event_type=MessageTemplate.EventType.COMMISSION_PAID,
        ).first()
        self.assertIsNotNone(notif)
        self.assertIn("Vendedor Teste", notif.message_body)
        self.assertIn("06/2026", notif.message_body)
        mock_delay.assert_called_once()

    @patch("app.apps.notifications.tasks.send_whatsapp_notification.delay")
    def test_fallback_body_when_no_template(self, mock_delay):
        MessageTemplate.objects.all().delete()

        from app.apps.notifications.tasks import create_and_send_notification

        notif = create_and_send_notification(
            tenant=self.tenant,
            event_type=MessageTemplate.EventType.SELLER_CREDENTIALS,
            channel=MessageTemplate.Channel.WHATSAPP,
            recipient="31999999999",
            seller=self.seller,
            context={
                "vendedor": "Maria",
                "usuario": "maria",
                "senha": "xyz789",
            },
        )

        self.assertIsNotNone(notif.pk)
        self.assertIn("Maria", notif.message_body)
        self.assertIn("maria", notif.message_body)
        self.assertIn("xyz789", notif.message_body)
        mock_delay.assert_called_once()

    @patch("app.apps.notifications.tasks.WhatsappClient")
    def test_send_whatsapp_notification_success(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        notif = Notification.objects.create(
            tenant=self.tenant,
            seller=self.seller,
            event_type=MessageTemplate.EventType.SELLER_CREDENTIALS,
            channel=MessageTemplate.Channel.WHATSAPP,
            recipient="31999999999",
            message_body="teste",
        )

        from app.apps.notifications.tasks import send_whatsapp_notification
        send_whatsapp_notification(notif.uuid)

        notif.refresh_from_db()
        self.assertEqual(notif.status, Notification.Status.SENT)

    @patch("app.apps.notifications.tasks.WhatsappClient")
    def test_send_whatsapp_notification_max_retries_exceeded(self, mock_client_class):
        mock_client = MagicMock()
        mock_client.send_message.side_effect = Exception("API Error")
        mock_client_class.return_value = mock_client

        notif = Notification.objects.create(
            tenant=self.tenant,
            seller=self.seller,
            event_type=MessageTemplate.EventType.SELLER_CREDENTIALS,
            channel=MessageTemplate.Channel.WHATSAPP,
            recipient="31999999999",
            message_body="teste",
            retry_count=3,
        )

        from app.apps.notifications.tasks import send_whatsapp_notification
        send_whatsapp_notification(notif.uuid)

        notif.refresh_from_db()
        self.assertEqual(notif.status, Notification.Status.FAILED)
        self.assertIn("Max retries", notif.error_log)

    @patch("app.apps.notifications.tasks.WhatsappClient")
    def test_send_whatsapp_notification_retry_on_failure(self, mock_client_class):
        mock_client = MagicMock()
        mock_client.send_message.side_effect = Exception("Timeout")
        mock_client_class.return_value = mock_client

        notif = Notification.objects.create(
            tenant=self.tenant,
            seller=self.seller,
            event_type=MessageTemplate.EventType.SELLER_CREDENTIALS,
            channel=MessageTemplate.Channel.WHATSAPP,
            recipient="31999999999",
            message_body="teste",
        )

        from app.apps.notifications.tasks import send_whatsapp_notification
        try:
            send_whatsapp_notification(notif.uuid)
        except Exception:
            pass

        notif.refresh_from_db()
        self.assertGreater(notif.retry_count, 0)
        self.assertIn("Timeout", notif.error_log)

    def test_skip_non_pending_notification(self):
        notif = Notification.objects.create(
            tenant=self.tenant,
            seller=self.seller,
            event_type=MessageTemplate.EventType.SELLER_CREDENTIALS,
            channel=MessageTemplate.Channel.WHATSAPP,
            recipient="31999999999",
            message_body="teste",
            status=Notification.Status.SENT,
        )

        from app.apps.notifications.tasks import send_whatsapp_notification
        send_whatsapp_notification(notif.uuid)

        notif.refresh_from_db()
        self.assertEqual(notif.status, Notification.Status.SENT)
