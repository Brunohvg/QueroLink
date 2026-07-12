"""PROMPT 47 - testes de API de SellerDayJustification (LOTE 9)."""

from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.core.cache import cache
from rest_framework.test import APIClient

from app.apps.accounts.models import Tenant, User
from app.apps.sellers.models import Seller, SellerDayJustification


class DayJustificationApiTest(TestCase):
    def setUp(self):
        cache.clear()
        self.tenant = Tenant.objects.create(
            company_name='API JD', slug='api-jd',
            default_commission_rate=Decimal('0.01'),
        )
        self.tenant2 = Tenant.objects.create(
            company_name='API JD2', slug='api-jd2',
            default_commission_rate=Decimal('0.01'),
        )
        self.admin = User.objects.create_user(
            username='admin_jd',
            role=User.Role.ADMIN, tenant=self.tenant,
        )
        self.manager = User.objects.create_user(
            username='mgr_jd',
            role=User.Role.MANAGER, tenant=self.tenant,
        )
        self.seller_user = User.objects.create_user(
            username='sel_jd',
            role=User.Role.SELLER, tenant=self.tenant,
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant, user=self.seller_user, name='Seller JD',
            phone='55999990001', commission_rate=Decimal('0.01'),
        )
        self.seller_b_user = User.objects.create_user(
            username='sel_b_jd',
            role=User.Role.SELLER, tenant=self.tenant,
        )
        self.seller_b = Seller.objects.create(
            tenant=self.tenant, user=self.seller_b_user, name='Seller B',
            phone='55999990009', commission_rate=Decimal('0.01'),
        )
        # tenant2
        self.admin2 = User.objects.create_user(
            username='admin2_jd',
            role=User.Role.ADMIN, tenant=self.tenant2,
        )
        self.seller2 = Seller.objects.create(
            tenant=self.tenant2, user=User.objects.create_user(
                username='sel2_jd',
                role=User.Role.SELLER, tenant=self.tenant2,
            ),
            name='Seller T2', phone='55999990002',
            commission_rate=Decimal('0.01'),
        )
        self.list_url = reverse('api-day-justification-list')

    def _auth(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def _payload(self, seller=None, d='2026-07-12', reason='ATESTADO'):
        return {
            'seller': str((seller or self.seller).uuid),
            'date': d, 'reason': reason, 'notes': 'ok',
        }

    # 1 - ADMIN cria
    def test_admin_creates(self):
        client = self._auth(self.admin)
        resp = client.post(self.list_url, self._payload(), format='json')
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(SellerDayJustification.objects.count(), 1)

    # 2 - MANAGER cria
    def test_manager_creates(self):
        client = self._auth(self.manager)
        resp = client.post(self.list_url, self._payload(), format='json')
        self.assertEqual(resp.status_code, 201)

    # 3 - SELLER recebe 403
    def test_seller_forbidden(self):
        client = self._auth(self.seller_user)
        self.assertEqual(
            client.get(self.list_url).status_code, 403,
        )
        self.assertEqual(
            client.post(self.list_url, self._payload(), format='json').status_code,
            403,
        )

    # 4 - outro tenant recebe 404
    def test_other_tenant_404(self):
        j = SellerDayJustification.objects.create(
            tenant=self.tenant2, seller=self.seller2, date=date(2026, 7, 12),
            reason='FALTA', created_by=self.admin2, updated_by=self.admin2,
        )
        client = self._auth(self.admin)
        url = reverse('api-day-justification-detail', args=[j.uuid])
        self.assertEqual(client.get(url).status_code, 404)

    # 5 - lista limitada ao tenant
    def test_list_limited_to_tenant(self):
        SellerDayJustification.objects.create(
            tenant=self.tenant, seller=self.seller, date=date(2026, 7, 12),
            reason='ATESTADO', created_by=self.admin, updated_by=self.admin,
        )
        SellerDayJustification.objects.create(
            tenant=self.tenant2, seller=self.seller2, date=date(2026, 7, 12),
            reason='FALTA', created_by=self.admin2, updated_by=self.admin2,
        )
        client = self._auth(self.admin)
        resp = client.get(self.list_url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data), 1)

    # 6 - filtro por seller
    def test_filter_by_seller(self):
        SellerDayJustification.objects.create(
            tenant=self.tenant, seller=self.seller, date=date(2026, 7, 12),
            reason='ATESTADO', created_by=self.admin, updated_by=self.admin,
        )
        SellerDayJustification.objects.create(
            tenant=self.tenant, seller=self.seller_b, date=date(2026, 7, 12),
            reason='FALTA', created_by=self.admin, updated_by=self.admin,
        )
        client = self._auth(self.admin)
        resp = client.get(self.list_url + f'?seller={self.seller.uuid}')
        self.assertEqual(len(resp.data), 1)
        self.assertEqual(
            resp.data[0]['seller_uuid'], str(self.seller.uuid),
        )

    # 7 - filtro por range
    def test_filter_by_range(self):
        for d in ['2026-07-01', '2026-07-10', '2026-07-20']:
            SellerDayJustification.objects.create(
                tenant=self.tenant, seller=self.seller, date=d,
                reason='FOLGA', created_by=self.admin, updated_by=self.admin,
            )
        client = self._auth(self.admin)
        resp = client.get(
            self.list_url + '?start_date=2026-07-05&end_date=2026-07-15',
        )
        dates = {r['date'] for r in resp.data}
        self.assertEqual(dates, {'2026-07-10'})

    # 8 - detalhe
    def test_detail(self):
        j = SellerDayJustification.objects.create(
            tenant=self.tenant, seller=self.seller, date=date(2026, 7, 12),
            reason='ATESTADO', created_by=self.admin, updated_by=self.admin,
        )
        client = self._auth(self.admin)
        url = reverse('api-day-justification-detail', args=[j.uuid])
        resp = client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['uuid'], str(j.uuid))

    # 9 - PATCH
    def test_patch(self):
        j = SellerDayJustification.objects.create(
            tenant=self.tenant, seller=self.seller, date=date(2026, 7, 12),
            reason='ATESTADO', created_by=self.admin, updated_by=self.admin,
        )
        client = self._auth(self.admin)
        url = reverse('api-day-justification-detail', args=[j.uuid])
        resp = client.patch(url, {'reason': 'FALTA'}, format='json')
        self.assertEqual(resp.status_code, 200)
        j.refresh_from_db()
        self.assertEqual(j.reason, 'FALTA')
        self.assertEqual(j.updated_by, self.admin)

    # 10 - DELETE
    def test_delete(self):
        j = SellerDayJustification.objects.create(
            tenant=self.tenant, seller=self.seller, date=date(2026, 7, 12),
            reason='ATESTADO', created_by=self.admin, updated_by=self.admin,
        )
        client = self._auth(self.admin)
        url = reverse('api-day-justification-detail', args=[j.uuid])
        resp = client.delete(url)
        self.assertEqual(resp.status_code, 204)
        self.assertFalse(
            SellerDayJustification.objects.filter(pk=j.pk).exists(),
        )

    # 11 - UUID invalido retorna 404
    def test_invalid_uuid_404(self):
        client = self._auth(self.admin)
        url = reverse(
            'api-day-justification-detail',
            args=['00000000-0000-0000-0000-000000000000'],
        )
        self.assertEqual(client.get(url).status_code, 404)

    # 12 - payload nao pode definir tenant
    def test_payload_cannot_set_tenant(self):
        client = self._auth(self.admin)
        payload = self._payload()
        payload['tenant'] = str(self.tenant2.uuid)
        resp = client.post(self.list_url, payload, format='json')
        self.assertEqual(resp.status_code, 201)
        j = SellerDayJustification.objects.get(uuid=resp.data['uuid']) \
            if 'uuid' in resp.data else SellerDayJustification.objects.first()
        self.assertEqual(j.tenant, self.tenant)

    # 13 - payload nao pode definir created_by
    def test_payload_cannot_set_created_by(self):
        client = self._auth(self.admin)
        payload = self._payload()
        payload['created_by'] = str(self.manager.pk)
        resp = client.post(self.list_url, payload, format='json')
        self.assertEqual(resp.status_code, 201)
        j = SellerDayJustification.objects.first()
        self.assertEqual(j.created_by, self.admin)

    # 14 - date serializada como YYYY-MM-DD
    def test_date_serialized_iso(self):
        SellerDayJustification.objects.create(
            tenant=self.tenant, seller=self.seller, date=date(2026, 7, 12),
            reason='ATESTADO', created_by=self.admin, updated_by=self.admin,
        )
        client = self._auth(self.admin)
        resp = client.get(self.list_url)
        self.assertEqual(resp.data[0]['date'], '2026-07-12')

    # 15 - reason_display em portugues
    def test_reason_display_pt(self):
        SellerDayJustification.objects.create(
            tenant=self.tenant, seller=self.seller, date=date(2026, 7, 12),
            reason='FERIAS', created_by=self.admin, updated_by=self.admin,
        )
        client = self._auth(self.admin)
        resp = client.get(self.list_url)
        self.assertEqual(resp.data[0]['reason_display'], 'Férias')

    # extra: FINANCEIRO nao gere operacional -> 403
    def test_financeiro_forbidden(self):
        fin = User.objects.create_user(
            username='fin_jd',
            role=User.Role.FINANCEIRO, tenant=self.tenant,
        )
        client = self._auth(fin)
        self.assertEqual(client.get(self.list_url).status_code, 403)
