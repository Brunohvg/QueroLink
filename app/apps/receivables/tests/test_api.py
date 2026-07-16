from unittest.mock import patch

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from app.apps.accounts.models import Tenant, User

from .helpers import make_boleto, make_seller, make_tenant, make_user
from .helpers import boleto_data


class BoletoApiScopeTests(APITestCase):
    def setUp(self):
        self.tenant = make_tenant()
        self.seller_user, self.seller = make_seller(self.tenant, 'seller-a')
        self.other_user, self.other_seller = make_seller(self.tenant, 'seller-b')
        self.manager = make_user(self.tenant, 'manager', User.Role.MANAGER)
        self.mine = make_boleto(self.tenant, self.seller, self.seller_user, gateway_charge_id='ch_a')
        self.other = make_boleto(self.tenant, self.other_seller, self.other_user, gateway_charge_id='ch_b')

    def test_seller_list_is_paginated_and_only_own(self):
        self.client.force_authenticate(self.seller_user)
        response = self.client.get(reverse('api-boleto-list'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['uuid'], str(self.mine.uuid))

    def test_seller_cannot_see_or_cancel_other_boleto(self):
        self.client.force_authenticate(self.seller_user)
        detail = reverse('api-boleto-detail', args=[self.other.uuid])
        cancel = reverse('api-boleto-cancel', args=[self.other.uuid])
        self.assertEqual(self.client.get(detail).status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(self.client.post(cancel).status_code, status.HTTP_404_NOT_FOUND)

    @patch('app.apps.receivables.api.cancel_boleto')
    def test_manager_can_cancel_tenant_boleto(self, cancel_mock):
        cancel_mock.return_value = self.other
        self.client.force_authenticate(self.manager)
        response = self.client.post(reverse('api-boleto-cancel', args=[self.other.uuid]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        cancel_mock.assert_called_once_with(self.other, self.manager)

    def test_starter_is_forbidden(self):
        tenant = make_tenant('Starter', plan=Tenant.Plan.STARTER)
        user, _seller = make_seller(tenant, 'starter-seller')
        self.client.force_authenticate(user)
        response = self.client.get(reverse('api-boleto-list'))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_other_tenant_is_never_visible(self):
        other_tenant = make_tenant('Outra Loja')
        other_manager = make_user(other_tenant, 'other-manager', User.Role.MANAGER)
        self.client.force_authenticate(other_manager)
        response = self.client.get(reverse('api-boleto-detail', args=[self.mine.uuid]))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    @patch('app.apps.receivables.serializers.create_boleto')
    def test_seller_create_uses_own_profile(self, create_mock):
        create_mock.return_value = self.mine
        self.client.force_authenticate(self.seller_user)
        response = self.client.post(
            reverse('api-boleto-list'), boleto_data(), format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        args = create_mock.call_args.args
        self.assertEqual(args[:3], (self.tenant, self.seller, self.seller_user))

    def test_manager_summary(self):
        self.mine.status = 'PAGO'
        self.mine.paid_amount_cents = 15100
        from django.utils import timezone
        self.mine.paid_at = timezone.now()
        self.mine.save(update_fields=['status', 'paid_amount_cents', 'paid_at'])
        self.client.force_authenticate(self.manager)
        response = self.client.get(reverse('api-boleto-summary'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['pagos_mes_cents'], 15100)
        self.assertEqual(response.data['aguardando_lancamento'], 1)
