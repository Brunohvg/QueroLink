from datetime import timedelta
from unittest.mock import patch

from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from app.apps.accounts.models import User

from ..tasks import send_boleto_email, send_boleto_manager_digest
from .helpers import make_boleto, make_seller, make_tenant, make_user


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class BoletoNotificationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.tenant = make_tenant()
        self.user, self.seller = make_seller(self.tenant)
        self.manager = make_user(self.tenant, 'digest-manager', User.Role.MANAGER)
        self.manager.email = 'manager@example.com'
        self.manager.save(update_fields=['email'])

    def test_email_reminder_is_deduplicated(self):
        boleto = make_boleto(self.tenant, self.seller, self.user)
        self.assertEqual(send_boleto_email.run(str(boleto.uuid), 'due_tomorrow'), 'sent')
        self.assertEqual(send_boleto_email.run(str(boleto.uuid), 'due_tomorrow'), 'duplicate')
        self.assertEqual(len(mail.outbox), 1)

    @patch('app.apps.receivables.tasks.tenant_operational', return_value=True)
    def test_digest_only_sends_when_there_is_content(self, _operational_mock):
        send_boleto_manager_digest.run()
        self.assertEqual(len(mail.outbox), 0)
        make_boleto(
            self.tenant, self.seller, self.user,
            due_date=timezone.localdate() + timedelta(days=2),
        )
        send_boleto_manager_digest.run()
        self.assertEqual(len(mail.outbox), 1)
