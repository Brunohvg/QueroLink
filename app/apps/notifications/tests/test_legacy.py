from datetime import time, timedelta, date
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from app.apps.accounts.models import Tenant, User
from app.apps.sellers.models import Seller
from app.apps.sales.models import Sale
from app.apps.notifications.models import Notification, MessageTemplate


class BaseDailyReminderTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='Test Reminder',
            slug='test-reminder',
            is_active=True,
            daily_reminder_enabled=True,
            daily_reminder_time=time(20, 0),
            trial_ends_at=timezone.now() + timedelta(days=30),
        )
        self.seller_user = User.objects.create_user(
            username='seller_rem', password='test123',
            role=User.Role.SELLER, tenant=self.tenant,
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant,
            user=self.seller_user,
            name='Vendedor Teste',
            phone='55999999999',
            is_active=True,
        )
        self.seller2 = Seller.objects.create(
            tenant=self.tenant,
            user=User.objects.create_user(
                username='seller_rem2', password='test123',
                role=User.Role.SELLER, tenant=self.tenant,
            ),
            name='Vendedor2',
            phone='55999999998',
            is_active=True,
        )

    def _set_time(self, hour, minute=0):
        return timezone.make_aware(
            timezone.datetime(2026, 7, 3, hour, minute),
        )


class TestDailyReminder(BaseDailyReminderTest):
    @patch('app.apps.notifications.tasks.send_whatsapp_notification')
    @patch('app.apps.notifications.tasks.create_and_send_notification')
    def test_sends_only_to_sellers_without_sale(self, mock_create, mock_send):
        Sale.objects.create(
            tenant=self.tenant, seller=self.seller,
            origin=Sale.Origin.MANUAL, amount=10000,
            sale_date=date(2026, 7, 3),
            status='ATIVA',
        )

        mock_now = timezone.make_aware(
            timezone.datetime(2026, 7, 3, 20, 5),
        )
        with patch('django.utils.timezone.localtime', return_value=mock_now):
            from app.apps.notifications.tasks import send_daily_entry_reminders
            send_daily_entry_reminders()

        seller_notifications = Notification.objects.filter(
            seller=self.seller,
            event_type=MessageTemplate.EventType.DAILY_REMINDER,
        )
        self.assertEqual(seller_notifications.count(), 0)
        mock_create.assert_called_once()
        self.assertEqual(mock_create.call_args.kwargs['seller'], self.seller2)

    @patch('app.apps.notifications.tasks.send_whatsapp_notification')
    @patch('app.apps.notifications.tasks.create_and_send_notification')
    def test_does_not_send_duplicate_same_day(self, mock_create, mock_send):
        Notification.objects.create(
            tenant=self.tenant, seller=self.seller,
            event_type=MessageTemplate.EventType.DAILY_REMINDER,
            channel='whatsapp', recipient='55999999999',
            message_body='Lembrete',
            created_at=timezone.now(),
        )

        mock_now = timezone.make_aware(
            timezone.datetime(2026, 7, 3, 20, 5),
        )
        with patch('django.utils.timezone.localtime', return_value=mock_now):
            from app.apps.notifications.tasks import send_daily_entry_reminders
            send_daily_entry_reminders()

        count = Notification.objects.filter(
            seller=self.seller,
            event_type=MessageTemplate.EventType.DAILY_REMINDER,
        ).count()
        self.assertEqual(count, 1)

    @patch('app.apps.notifications.tasks.send_whatsapp_notification')
    @patch('app.apps.notifications.tasks.create_and_send_notification')
    def test_does_not_send_on_sunday(self, mock_create, mock_send):
        mock_now = timezone.make_aware(
            timezone.datetime(2026, 7, 5, 20, 5),
        )
        with patch('django.utils.timezone.localtime', return_value=mock_now):
            from app.apps.notifications.tasks import send_daily_entry_reminders
            send_daily_entry_reminders()

        count = Notification.objects.filter(
            event_type=MessageTemplate.EventType.DAILY_REMINDER,
        ).count()
        self.assertEqual(count, 0)

    @patch('app.apps.notifications.tasks.send_whatsapp_notification')
    @patch('app.apps.notifications.tasks.create_and_send_notification')
    def test_respects_disabled_flag(self, mock_create, mock_send):
        self.tenant.daily_reminder_enabled = False
        self.tenant.save()

        mock_now = timezone.make_aware(
            timezone.datetime(2026, 7, 3, 20, 5),
        )
        with patch('django.utils.timezone.localtime', return_value=mock_now):
            from app.apps.notifications.tasks import send_daily_entry_reminders
            send_daily_entry_reminders()

        count = Notification.objects.filter(
            event_type=MessageTemplate.EventType.DAILY_REMINDER,
        ).count()
        self.assertEqual(count, 0)

    @patch('app.apps.notifications.tasks.send_whatsapp_notification')
    @patch('app.apps.notifications.tasks.create_and_send_notification')
    def test_respects_time_window(self, mock_create, mock_send):
        mock_now = timezone.make_aware(
            timezone.datetime(2026, 7, 3, 19, 0),
        )
        with patch('django.utils.timezone.localtime', return_value=mock_now):
            from app.apps.notifications.tasks import send_daily_entry_reminders
            send_daily_entry_reminders()

        count = Notification.objects.filter(
            event_type=MessageTemplate.EventType.DAILY_REMINDER,
        ).count()
        self.assertEqual(count, 0)
