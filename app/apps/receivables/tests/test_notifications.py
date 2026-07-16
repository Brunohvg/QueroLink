from datetime import timedelta
from unittest.mock import patch

from django.core import mail
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from app.apps.accounts.models import User
from tempfile import TemporaryDirectory

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
        html = mail.outbox[0].alternatives[0][0]
        self.assertIn(self.tenant.company_name, html)
        self.assertIn('vidalys-merito-logo.png', html)

    def test_invoice_email_includes_pdf_and_xml_attachments(self):
        boleto = make_boleto(self.tenant, self.seller, self.user)
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            boleto.invoice_pdf.save(
                'nota.pdf', SimpleUploadedFile(
                    'nota.pdf', b'%PDF-1.4 test', 'application/pdf',
                ),
            )
            boleto.invoice_xml.save(
                'nota.xml', SimpleUploadedFile(
                    'nota.xml', b'<nfe/>', 'application/xml',
                ),
            )
            self.assertEqual(
                send_boleto_email.run(str(boleto.uuid), 'invoice', True), 'sent',
            )
        filenames = [attachment[0] for attachment in mail.outbox[0].attachments]
        self.assertEqual(filenames, ['nota-fiscal.pdf', 'nota-fiscal.xml'])

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
