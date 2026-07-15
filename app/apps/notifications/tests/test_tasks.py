from unittest.mock import call, patch, MagicMock
from django.test import TestCase, override_settings
from django.conf import settings
from app.apps.accounts.models import Tenant, User
from app.apps.sellers.models import Seller
from app.apps.commissions.models import CommissionPeriod, SellerCommission
from app.apps.notifications.models import Notification, MessageTemplate, PushSubscription
from datetime import timedelta
from django.utils import timezone as dtz

from app.services.messaging.whatsapp import WhatsappClient, InvalidNumberError


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
    def test_create_and_send_notification_persists_secondary_body(self, mock_delay):
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
            secondary_body="abc123",
        )

        self.assertEqual(notif.secondary_body, "abc123")
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
        self.assertEqual(notif.secondary_body, "senha123")
        self.assertNotIn("*", notif.secondary_body)
        self.assertNotIn("`", notif.secondary_body)
        self.assertNotIn("\n", notif.secondary_body)
        self.assertFalse(notif.secondary_body.startswith("Senha"))
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
        self.tenant.whatsapp_instance_id = 'test-instance'
        self.tenant.save()

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

    @patch("app.apps.notifications.tasks.time.sleep")
    @patch("app.apps.notifications.tasks.WhatsappClient")
    def test_send_whatsapp_notification_with_secondary_body_sends_two_messages(
        self, mock_client_class, mock_sleep
    ):
        self.tenant.whatsapp_instance_id = 'test-instance'
        self.tenant.save()

        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        notif = Notification.objects.create(
            tenant=self.tenant,
            seller=self.seller,
            event_type=MessageTemplate.EventType.SELLER_CREDENTIALS,
            channel=MessageTemplate.Channel.WHATSAPP,
            recipient="31999999999",
            message_body="corpo",
            secondary_body="Abc#234567",
        )

        from app.apps.notifications.tasks import send_whatsapp_notification
        send_whatsapp_notification(notif.uuid)

        notif.refresh_from_db()
        self.assertEqual(notif.status, Notification.Status.SENT)
        mock_sleep.assert_called_once_with(1)
        self.assertEqual(mock_client.send_message.call_count, 2)
        self.assertEqual(
            mock_client.send_message.call_args_list,
            [
                call("31999999999", "corpo"),
                call("31999999999", "Abc#234567"),
            ],
        )

    @patch("app.apps.notifications.tasks.time.sleep")
    @patch("app.apps.notifications.tasks.WhatsappClient")
    def test_second_message_failure_keeps_pending_and_retries(
        self, mock_client_class, mock_sleep
    ):
        self.tenant.whatsapp_instance_id = 'test-instance'
        self.tenant.save()

        mock_client = MagicMock()
        mock_client.send_message.side_effect = [None, Exception("Second failed")]
        mock_client_class.return_value = mock_client

        notif = Notification.objects.create(
            tenant=self.tenant,
            seller=self.seller,
            event_type=MessageTemplate.EventType.SELLER_CREDENTIALS,
            channel=MessageTemplate.Channel.WHATSAPP,
            recipient="31999999999",
            message_body="corpo",
            secondary_body="Abc#234567",
        )

        from app.apps.notifications.tasks import send_whatsapp_notification
        try:
            send_whatsapp_notification(notif.uuid)
        except Exception:
            pass

        notif.refresh_from_db()
        self.assertEqual(notif.status, Notification.Status.PENDING)
        self.assertEqual(notif.retry_count, 1)
        self.assertIn("Second failed", notif.error_log)
        mock_sleep.assert_called_once_with(1)

    @patch("app.apps.notifications.tasks.WhatsappClient")
    def test_other_event_without_secondary_body_sends_one_message(self, mock_client_class):
        self.tenant.whatsapp_instance_id = 'test-instance'
        self.tenant.save()

        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        notif = Notification.objects.create(
            tenant=self.tenant,
            seller=self.seller,
            event_type=MessageTemplate.EventType.COMMISSION_PAID,
            channel=MessageTemplate.Channel.WHATSAPP,
            recipient="31999999999",
            message_body="comissao paga",
        )

        from app.apps.notifications.tasks import send_whatsapp_notification
        send_whatsapp_notification(notif.uuid)

        notif.refresh_from_db()
        self.assertEqual(notif.status, Notification.Status.SENT)
        self.assertFalse(notif.secondary_body)
        mock_client.send_message.assert_called_once_with("31999999999", "comissao paga")

    @patch("app.apps.notifications.tasks.WhatsappClient")
    def test_send_whatsapp_notification_max_retries_exceeded(self, mock_client_class):
        self.tenant.whatsapp_instance_id = 'test-instance'
        self.tenant.save()

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
        self.tenant.whatsapp_instance_id = 'test-instance'
        self.tenant.save()

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

    @patch("app.apps.notifications.tasks.send_whatsapp_notification.delay")
    def test_daily_reminder_task_runs_without_error(self, mock_delay):
        from datetime import time
        from django.utils import timezone as dtz
        from app.apps.notifications.tasks import send_daily_entry_reminders

        self.tenant.daily_reminder_enabled = True
        self.tenant.daily_reminder_time = time(12, 0)
        self.tenant.save()
        CommissionPeriod.objects.create(
            tenant=self.tenant,
            month=7,
            year=2026,
            start_date=dtz.datetime(2026, 7, 1).date(),
            end_date=dtz.datetime(2026, 7, 31).date(),
            expected_working_days=23,
            status=CommissionPeriod.Status.ABERTA,
        )

        fake_now = dtz.make_aware(
            dtz.datetime(2026, 7, 3, 12, 5, 0)
        ).astimezone(dtz.get_current_timezone())

        with patch('app.apps.notifications.tasks.timezone') as mock_tz:
            mock_tz.localtime.return_value = fake_now
            with patch('app.apps.notifications.tasks.timezone.now') as mock_now:
                mock_now.return_value = dtz.datetime(2026, 7, 3, 12, 5, 0)

                send_daily_entry_reminders()

        notif = Notification.objects.filter(
            seller=self.seller,
            event_type=MessageTemplate.EventType.DAILY_REMINDER,
        ).first()
        self.assertIsNotNone(notif, "DAILY_REMINDER notification should have been created")
        self.assertIn("ainda nao lancou", notif.message_body)
        mock_delay.assert_called_once_with(notif.uuid)

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

    @override_settings(WHATSAPP_API_BASE_URL='https://api.test.com', WHATSAPP_ALLOW_SHARED_INSTANCE=False)
    @patch("app.apps.notifications.tasks.WhatsappClient")
    def test_tenant_without_instance_no_shared_flag_fails(self, mock_client_class):
        self.tenant.whatsapp_instance_id = ''
        self.tenant.save()

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
        self.assertEqual(notif.status, Notification.Status.FAILED)
        self.assertIn('Tenant sem instancia WhatsApp', notif.error_log)
        mock_client_class.assert_not_called()

    @override_settings(
        WHATSAPP_API_BASE_URL='https://api.test.com',
        WHATSAPP_ALLOW_SHARED_INSTANCE=True,
        WHATSAPP_INSTANCE='shared-instance',
        WHATSAPP_API_KEY='shared-key',
    )
    @patch("app.apps.notifications.tasks.WhatsappClient")
    def test_tenant_without_instance_shared_flag_uses_global(self, mock_client_class):
        self.tenant.whatsapp_instance_id = ''
        self.tenant.save()

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
        mock_client_class.assert_called_once_with(
            instance='shared-instance',
            api_key='shared-key',
        )

    @override_settings(WHATSAPP_API_BASE_URL='https://api.test.com', WHATSAPP_ALLOW_SHARED_INSTANCE=True)
    @patch("app.apps.notifications.tasks.WhatsappClient")
    def test_tenant_with_own_instance_uses_own(self, mock_client_class):
        self.tenant.whatsapp_instance_id = 'my-instance'
        self.tenant.whatsapp_token = 'my-token'
        self.tenant.save()

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
        mock_client_class.assert_called_once_with(
            instance='my-instance',
            api_key='my-token',
        )


class FormatNumberTest(TestCase):
    def setUp(self):
        self.client = WhatsappClient(instance='test', api_key='test')

    def test_format_ddd_with_9(self):
        result = self.client._format_number('(31) 98888-7777')
        self.assertEqual(result, '5531988887777')

    def test_format_10_digits(self):
        result = self.client._format_number('31 3222-1111')
        self.assertEqual(result, '553132221111')

    def test_format_ddd_55(self):
        result = self.client._format_number('(55) 99999-8888')
        self.assertEqual(result, '5555999998888')

    def test_format_with_plus55(self):
        result = self.client._format_number('+55 31 98888-7777')
        self.assertEqual(result, '5531988887777')

    def test_format_with_leading_zero_operator(self):
        result = self.client._format_number('0 31 98888-7777')
        self.assertEqual(result, '5531988887777')

    def test_format_already_13_digits(self):
        result = self.client._format_number('5531988887777')
        self.assertEqual(result, '5531988887777')

    def test_format_already_12_digits(self):
        result = self.client._format_number('553132221111')
        self.assertEqual(result, '553132221111')

    def test_format_invalid_number(self):
        with self.assertRaises(InvalidNumberError):
            self.client._format_number('123')

    def test_invalid_number_task_no_retry(self):
        tenant = Tenant.objects.create(company_name="Test", cnpj="33333333333333")
        tenant.whatsapp_instance_id = 'test-instance'
        tenant.save()

        notif = Notification.objects.create(
            tenant=tenant,
            event_type=MessageTemplate.EventType.SELLER_CREDENTIALS,
            channel=MessageTemplate.Channel.WHATSAPP,
            recipient="123",
            message_body="teste",
        )
        from app.apps.notifications.tasks import send_whatsapp_notification
        send_whatsapp_notification(notif.uuid)

        notif.refresh_from_db()
        self.assertEqual(notif.status, Notification.Status.FAILED)
        self.assertIn('invalido', notif.error_log.lower())
        self.assertEqual(notif.retry_count, 0)


class FormatBrlCentsTest(TestCase):
    def test_format_thousands(self):
        from app.apps.notifications.tasks import format_brl_cents
        self.assertEqual(format_brl_cents(123456), 'R$ 1.234,56')

    def test_format_small_value(self):
        from app.apps.notifications.tasks import format_brl_cents
        self.assertEqual(format_brl_cents(500), 'R$ 5,00')

    def test_format_zero(self):
        from app.apps.notifications.tasks import format_brl_cents
        self.assertEqual(format_brl_cents(0), 'R$ 0,00')

    def test_connection_error_alias_works(self):
        from app.services.messaging.whatsapp import ConnectionError, EvolutionConnectionError
        self.assertIs(ConnectionError, EvolutionConnectionError)


class RetryAndRescueTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(company_name="RetryTest", cnpj="55555555555555")
        self.tenant.whatsapp_instance_id = 'test-instance'
        self.tenant.save()
        self.user = User.objects.create_user(
            username="vendedor_retry", password="pass",
            role=User.Role.SELLER, tenant=self.tenant,
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant, name="Retry Seller", phone="31999999999",
            user=self.user,
        )

    @patch("app.apps.notifications.tasks.WhatsappClient")
    def test_retry_increments_count_and_stays_pending(self, mock_client_class):
        mock_client = MagicMock()
        mock_client.send_message.side_effect = Exception("Server error")
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
        self.assertEqual(notif.status, Notification.Status.PENDING)
        self.assertEqual(notif.retry_count, 1)
        self.assertIn("Server error", notif.error_log)

    @patch("app.apps.notifications.tasks.WhatsappClient")
    def test_three_failures_mark_failed(self, mock_client_class):
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

    @patch("app.apps.notifications.tasks.send_whatsapp_notification.delay")
    def test_rescue_requeues_old_pending(self, mock_delay):
        now = dtz.now()
        old_pending = Notification.objects.create(
            tenant=self.tenant,
            seller=self.seller,
            event_type=MessageTemplate.EventType.DAILY_REMINDER,
            channel=MessageTemplate.Channel.WHATSAPP,
            recipient="31999999999",
            message_body="teste",
            status=Notification.Status.PENDING,
            retry_count=0,
        )
        Notification.objects.filter(uuid=old_pending.uuid).update(
            created_at=now - timedelta(hours=1),
        )

        from app.apps.notifications.tasks import requeue_stuck_notifications
        requeue_stuck_notifications()

        mock_delay.assert_called_once_with(old_pending.uuid)

    @patch("app.apps.notifications.tasks.send_whatsapp_notification.delay")
    def test_rescue_ignores_recent_and_sent(self, mock_delay):
        now = dtz.now()
        recent = Notification.objects.create(
            tenant=self.tenant,
            seller=self.seller,
            event_type=MessageTemplate.EventType.DAILY_REMINDER,
            channel=MessageTemplate.Channel.WHATSAPP,
            recipient="31999999999",
            message_body="teste",
            status=Notification.Status.PENDING,
            retry_count=0,
        )
        Notification.objects.filter(uuid=recent.uuid).update(
            created_at=now - timedelta(minutes=5),
        )

        already_sent = Notification.objects.create(
            tenant=self.tenant,
            seller=self.seller,
            event_type=MessageTemplate.EventType.DAILY_REMINDER,
            channel=MessageTemplate.Channel.WHATSAPP,
            recipient="31999999999",
            message_body="teste",
            status=Notification.Status.SENT,
            retry_count=0,
        )
        Notification.objects.filter(uuid=already_sent.uuid).update(
            created_at=now - timedelta(hours=1),
        )

        from app.apps.notifications.tasks import requeue_stuck_notifications
        requeue_stuck_notifications()

        mock_delay.assert_not_called()


class WebPushTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(company_name="PushTest", cnpj="66666666666666")
        self.user = User.objects.create_user(
            username="push_seller",
            password="pass",
            role=User.Role.SELLER,
            tenant=self.tenant,
        )

    def test_push_subscribe_creates_subscription(self):
        sub = PushSubscription.objects.create(
            user=self.user,
            tenant=self.tenant,
            endpoint='https://fcm.googleapis.com/fcm/send/test123',
            p256dh='BP123abc==',
            auth='auth123==',
        )
        self.assertTrue(sub.is_active)
        self.assertEqual(sub.user, self.user)

    # NOTA: usamos override_settings(VAPID_PRIVATE_KEY=...) e nao
    # patch.object(settings, ..., create=True). Se o cache do LazySettings
    # estiver limpo (ex.: apos outro teste com override_settings), o mock
    # registra local=False e no teardown chama delattr(settings, ...),
    # apagando VAPID_PRIVATE_KEY do Settings pelo resto do processo. Isso
    # fazia testes posteriores (fechamento -> send_push) estourarem
    # AttributeError. override_settings restaura sem apagar o atributo.
    def test_push_with_410_endpoint_marked_inactive(self):
        sub = PushSubscription.objects.create(
            user=self.user,
            tenant=self.tenant,
            endpoint='https://expired.endpoint/',
            p256dh='BP123abc==',
            auth='auth123==',
        )

        from unittest.mock import patch, MagicMock
        from app.services.messaging.push import send_push_notification
        from pywebpush import WebPushException

        with patch('pywebpush.webpush') as mock_webpush:
            mock_webpush.side_effect = WebPushException(
                'Gone', response=MagicMock(status_code=410),
            )
            with override_settings(VAPID_PRIVATE_KEY='test-key'):
                send_push_notification(
                    user=self.user,
                    title='Test',
                    body='Test body',
                )

        sub.refresh_from_db()
        self.assertFalse(sub.is_active)

    def test_send_push_notification_called_for_active_subs(self):
        from unittest.mock import patch

        sub1 = PushSubscription.objects.create(
            user=self.user,
            tenant=self.tenant,
            endpoint='https://endpoint1.test/',
            p256dh='BP1==',
            auth='auth1==',
        )
        sub2 = PushSubscription.objects.create(
            user=self.user,
            tenant=self.tenant,
            endpoint='https://endpoint2.test/',
            p256dh='BP2==',
            auth='auth2==',
        )

        with patch('pywebpush.webpush') as mock_webpush:
            with override_settings(VAPID_PRIVATE_KEY='test-key'):
                from app.services.messaging.push import send_push_notification
                send_push_notification(
                    user=self.user,
                    title='Test',
                    body='Test body',
                )

        self.assertEqual(mock_webpush.call_count, 2)
