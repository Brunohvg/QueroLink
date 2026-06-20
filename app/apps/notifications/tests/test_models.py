from django.test import TestCase
from app.apps.accounts.models import Tenant, User
from app.apps.sellers.models import Seller
from app.apps.commissions.models import CommissionPeriod
from app.apps.notifications.models import Notification, MessageTemplate


class NotificationModelTest(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(company_name="Bibelo", cnpj="11111111111111")
        self.user = User.objects.create_user(
            username="vendedor_teste", password="pass", role=User.Role.SELLER, tenant=self.tenant
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant, name="Vendedor Teste", phone="31999999999", user=self.user,
        )

    def test_notification_without_order_using_seller(self):
        notif = Notification.objects.create(
            tenant=self.tenant,
            seller=self.seller,
            event_type=MessageTemplate.EventType.SELLER_CREDENTIALS,
            channel=MessageTemplate.Channel.WHATSAPP,
            recipient="31999999999",
            message_body="Ola Vendedor! Sua senha: abc123",
        )
        self.assertIsNotNone(notif.pk)
        self.assertIsNone(notif.order)
        self.assertEqual(notif.seller, self.seller)
        self.assertEqual(notif.tenant, self.tenant)

    def test_notification_with_commission_period(self):
        period = CommissionPeriod.objects.create(
            tenant=self.tenant, month=6, year=2026,
        )
        notif = Notification.objects.create(
            tenant=self.tenant,
            seller=self.seller,
            commission_period=period,
            event_type=MessageTemplate.EventType.COMMISSION_PAID,
            channel=MessageTemplate.Channel.WHATSAPP,
            recipient="31999999999",
            message_body="Comissao paga!",
        )
        self.assertEqual(notif.commission_period, period)
        self.assertEqual(notif.event_type, MessageTemplate.EventType.COMMISSION_PAID)

    def test_tenant_auto_filled_from_seller(self):
        notif = Notification(
            seller=self.seller,
            event_type=MessageTemplate.EventType.SELLER_CREDENTIALS,
            channel=MessageTemplate.Channel.WHATSAPP,
            recipient="31999999999",
            message_body="teste",
        )
        notif.save()
        self.assertEqual(notif.tenant, self.tenant)

    def test_default_status_is_pending(self):
        notif = Notification.objects.create(
            tenant=self.tenant,
            seller=self.seller,
            event_type=MessageTemplate.EventType.SELLER_CREDENTIALS,
            channel=MessageTemplate.Channel.WHATSAPP,
            recipient="31999999999",
            message_body="teste",
        )
        self.assertEqual(notif.status, Notification.Status.PENDING)
        self.assertEqual(notif.retry_count, 0)


class MessageTemplateRenderTest(TestCase):
    def test_render_body_seller_credentials(self):
        tmpl = MessageTemplate(
            event_type=MessageTemplate.EventType.SELLER_CREDENTIALS,
            channel=MessageTemplate.Channel.WHATSAPP,
            body="Ola {{vendedor}}! Usuario: {{usuario}}. Senha: {{senha}}.",
        )
        result = tmpl.render_body({
            "vendedor": "Maria",
            "usuario": "maria",
            "senha": "abc123xyz",
        })
        self.assertEqual(result, "Ola Maria! Usuario: maria. Senha: abc123xyz.")

    def test_render_body_commission_paid(self):
        tmpl = MessageTemplate(
            event_type=MessageTemplate.EventType.COMMISSION_PAID,
            channel=MessageTemplate.Channel.WHATSAPP,
            body="{{vendedor}}, comissao {{periodo}}: {{valor}}",
        )
        result = tmpl.render_body({
            "vendedor": "Jose",
            "periodo": "06/2026",
            "valor": "R$ 150,00",
        })
        self.assertEqual(result, "Jose, comissao 06/2026: R$ 150,00")
