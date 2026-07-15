"""PROMPT 48 - integracao operacional das justificativas (LOTE 10)."""

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from app.apps.accounts.models import Tenant
from app.apps.sellers.models import Seller, SellerDayJustification
from app.apps.commissions.models import CommissionPeriod, SellerCommission
from app.apps.sales.models import Sale
from app.apps.commissions.day_status import (
    get_period_day_statuses, get_period_summary_bulk,
    LANCADO, JUSTIFICADO, PENDENTE, NAO_UTIL,
)
from app.apps.sellers.services import (
    create_day_justification, update_day_justification,
    delete_day_justification, replace_justification_with_sale,
    JustificationConflictError, JustificationLockedError,
)

User = get_user_model()

# Competencia 21/06/2026 (domingo) a 20/07/2026.
P_START = date(2026, 6, 21)
P_END = date(2026, 7, 20)
REF = date(2026, 8, 1)   # tudo liquidado


class _Base(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            company_name='Flow Co', slug='flow-co',
            default_commission_rate=Decimal('0.01'),
        )
        self.admin = User.objects.create_user(
            username='admin_flow', role=User.Role.ADMIN, tenant=self.tenant,
        )
        self.seller_user = User.objects.create_user(
            username='sel_flow', role=User.Role.SELLER, tenant=self.tenant,
        )
        self.seller = Seller.objects.create(
            tenant=self.tenant, user=self.seller_user, name='Seller Flow',
            phone='55999990001', commission_rate=Decimal('0.01'),
        )
        self.period = CommissionPeriod.objects.create(
            tenant=self.tenant, month=7, year=2026,
            start_date=P_START, end_date=P_END, label='Julho/2026',
        )

    def _sale(self, d, amount=100000, origin=Sale.Origin.MANUAL, status='ATIVA'):
        return Sale.objects.create(
            tenant=self.tenant, seller=self.seller, origin=origin,
            amount=amount, sale_date=d, status=status,
            created_by=self.seller_user,
        )

    def _just(self, d, reason='FALTA'):
        return create_day_justification(
            tenant=self.tenant, seller=self.seller, date=d,
            reason=reason, user=self.admin,
        )

    def _statuses(self):
        r = get_period_day_statuses(
            self.tenant, self.seller, self.period, reference_date=REF,
        )
        return {d['date']: d for d in r['days']}, r['summary']


class DayStatusHelperTest(_Base):
    def test_lancado(self):  # 1
        self._sale(date(2026, 6, 23))
        by_date, _ = self._statuses()
        self.assertEqual(by_date[date(2026, 6, 23)]['status'], LANCADO)

    def test_justificado(self):  # 2
        self._just(date(2026, 6, 24))
        by_date, _ = self._statuses()
        self.assertEqual(by_date[date(2026, 6, 24)]['status'], JUSTIFICADO)

    def test_pendente(self):  # 3
        by_date, _ = self._statuses()
        self.assertEqual(by_date[date(2026, 6, 25)]['status'], PENDENTE)

    def test_nao_util_domingo(self):  # 4
        by_date, _ = self._statuses()
        # 21/06/2026 e domingo
        self.assertEqual(P_START.weekday(), 6)
        self.assertEqual(by_date[P_START]['status'], NAO_UTIL)

    def test_two_sales_one_day(self):  # 5
        self._sale(date(2026, 6, 23), origin=Sale.Origin.MANUAL)
        self._sale(date(2026, 6, 23), origin=Sale.Origin.IMPORTADA)
        by_date, summary = self._statuses()
        row = by_date[date(2026, 6, 23)]
        self.assertEqual(row['status'], LANCADO)
        self.assertEqual(row['active_sales_count'], 2)
        # conta como 1 dia lancado
        self.assertEqual(summary['lancados'], 1)
        self.assertEqual(summary['launched_days_count'], 1)
        self.assertEqual(summary['resolved_days_count'], 1)

    def test_justification_reduces_pending(self):  # 9
        _, s0 = self._statuses()
        before = s0['pending_days']
        self._just(date(2026, 6, 25))
        _, s1 = self._statuses()
        self.assertEqual(s1['pending_days'], before - 1)
        self.assertEqual(s1['justificados'], 1)
        self.assertEqual(s1['justified_days_count'], 1)
        self.assertEqual(s1['resolved_days_count'], 1)
        self.assertEqual(
            s1['expected_working_days'],
            s1['launched_days_count']
            + s1['justified_days_count']
            + s1['pending_days_count'],
        )

    def test_pronto_when_zero_pending(self):  # 21
        # justifica/lanca todos os dias uteis do range
        d = P_START
        from app.apps.accounts.models import is_working_day
        while d <= P_END:
            if is_working_day(self.tenant, d):
                self._just(d)
            d += timedelta(days=1)
        _, s = self._statuses()
        self.assertEqual(s['pending_days'], 0)
        self.assertEqual(s['operational_status'], 'PRONTO')
        self.assertEqual(
            s['resolved_days_count'],
            s['launched_days_count'] + s['justified_days_count'],
        )

    def test_pendente_status(self):  # 22
        self._sale(date(2026, 6, 23))
        _, s = self._statuses()
        self.assertGreater(s['pending_days'], 0)
        self.assertEqual(s['operational_status'], 'PENDENTE')

    def test_seller_commission_justification_resolves_without_finance(self):
        self.period.start_date = date(2026, 6, 23)
        self.period.end_date = date(2026, 6, 24)
        self.period.expected_working_days = 2
        self.period.save()
        self._sale(date(2026, 6, 23), amount=100000)
        self._just(date(2026, 6, 24))
        sc = SellerCommission.objects.create(
            period=self.period, seller=self.seller,
            commission_rate=Decimal('0.01'), expected_working_days=2,
        )
        sc.recalculate()
        self.assertEqual(sc.total_sold_amount, 100000)
        self.assertEqual(sc.commission_amount, 1000)
        self.assertEqual(sc.submitted_days_count, 1)
        self.assertEqual(sc.missing_days_count, 0)
        self.assertEqual(
            sc.operational_status, SellerCommission.OperationalStatus.PRONTO,
        )

    def test_civil_dates_not_shifted(self):  # 25
        self._sale(date(2026, 6, 30))
        by_date, _ = self._statuses()
        self.assertIn(date(2026, 6, 30), by_date)
        self.assertEqual(by_date[date(2026, 6, 30)]['status'], LANCADO)

    def test_no_nplus1_per_day(self):  # 23
        for i in range(10):
            self._sale(P_START + timedelta(days=2 + i))
        with CaptureQueriesContext(connection) as ctx:
            get_period_day_statuses(
                self.tenant, self.seller, self.period, reference_date=REF,
            )
        self.assertLessEqual(len(ctx.captured_queries), 3)

    def test_no_nplus1_per_seller_bulk(self):  # 24
        sellers = [self.seller]
        for i in range(20):
            u = User.objects.create_user(
                username=f'sflow{i}', role=User.Role.SELLER, tenant=self.tenant,
            )
            sellers.append(Seller.objects.create(
                tenant=self.tenant, user=u, name=f'S{i}',
                phone=f'5599000{i:04d}', commission_rate=Decimal('0.01'),
            ))
        with CaptureQueriesContext(connection) as ctx:
            get_period_summary_bulk(
                self.tenant, self.period, sellers, reference_date=REF,
            )
        # constante (nao cresce com 21 vendedores)
        self.assertLessEqual(len(ctx.captured_queries), 4)


class FinancialZeroTest(_Base):
    def test_justification_does_not_change_total(self):  # 6
        self._sale(date(2026, 6, 23), amount=819489)
        total_before = Sale.objects.filter(
            tenant=self.tenant, status='ATIVA',
        ).count()
        self._just(date(2026, 6, 24))
        self.assertEqual(
            Sale.objects.filter(tenant=self.tenant, status='ATIVA').count(),
            total_before,
        )

    def test_justification_no_commission_no_ranking(self):  # 7, 8, 26, 27
        self._sale(date(2026, 6, 23), amount=1000000)
        from app.apps.commissions.services import (
            calculate_estimated_commission_for_period,
        )
        comm_before, total_before = calculate_estimated_commission_for_period(
            self.seller, self.period,
        )
        self._just(date(2026, 6, 24))
        comm_after, total_after = calculate_estimated_commission_for_period(
            self.seller, self.period,
        )
        self.assertEqual((comm_before, total_before), (comm_after, total_after))
        # nenhuma Sale ficticia/R$0 criada
        self.assertFalse(
            Sale.objects.filter(tenant=self.tenant, amount=0).exists(),
        )
        self.assertEqual(
            Sale.objects.filter(tenant=self.tenant).count(), 1,
        )


class ConflictAndReplaceTest(_Base):
    def test_justify_day_with_sale_conflict(self):  # 10
        self._sale(date(2026, 6, 23))
        with self.assertRaises(JustificationConflictError):
            self._just(date(2026, 6, 23))

    def test_sale_on_justified_day_api_conflict(self):  # 11
        self._just(date(2026, 6, 25))
        client = APIClient()
        client.force_authenticate(user=self.admin)
        resp = client.post(reverse('api-sale-list'), {
            'seller': str(self.seller.uuid), 'origin': 'MANUAL',
            'amount': 5000, 'sale_date': '2026-06-25',
        }, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('justificativa', str(resp.data).lower())
        # justificativa preservada, nenhuma venda criada
        self.assertTrue(SellerDayJustification.objects.filter(
            seller=self.seller, date=date(2026, 6, 25),
        ).exists())
        self.assertFalse(Sale.objects.filter(sale_date=date(2026, 6, 25)).exists())

    def test_replace_justification_with_sale(self):  # 12
        j = self._just(date(2026, 6, 25))
        sale = replace_justification_with_sale(
            justification=j, amount=819489, user=self.admin,
        )
        self.assertEqual(sale.amount, 819489)
        self.assertEqual(sale.sale_date, date(2026, 6, 25))
        self.assertFalse(
            SellerDayJustification.objects.filter(pk=j.pk).exists(),
        )

    def test_replace_rollback_preserves_justification(self):  # 13
        j = self._just(date(2026, 6, 25))
        pk = j.pk
        with patch(
            'app.apps.commissions.services.ensure_seller_commission',
            side_effect=RuntimeError('boom'),
        ):
            with self.assertRaises(RuntimeError):
                replace_justification_with_sale(
                    justification=j, amount=819489, user=self.admin,
                )
        # rollback: justificativa preservada (linha restaurada), venda nao criada
        self.assertTrue(
            SellerDayJustification.objects.filter(pk=pk).exists(),
        )
        self.assertFalse(
            Sale.objects.filter(sale_date=date(2026, 6, 25)).exists(),
        )


class LockedPeriodTest(_Base):
    def _lock(self, period_status, sc_status=None):
        self.period.status = period_status
        self.period.save()
        if sc_status:
            SellerCommission.objects.create(
                period=self.period, seller=self.seller,
                commission_rate=Decimal('0.01'), status=sc_status,
            )

    def test_fechada_blocks_create(self):  # 14
        self._lock(CommissionPeriod.Status.FECHADA)
        with self.assertRaises(JustificationLockedError):
            self._just(date(2026, 6, 25))

    def test_paga_blocks_delete(self):  # 15
        j = self._just(date(2026, 6, 25))
        self._lock(
            CommissionPeriod.Status.PAGA,
            sc_status=SellerCommission.Status.PAGA,
        )
        with self.assertRaises(JustificationLockedError):
            delete_day_justification(justification=j, user=self.admin)

    def test_reaberta_allows_crud(self):  # 16
        SellerCommission.objects.create(
            period=self.period, seller=self.seller,
            commission_rate=Decimal('0.01'),
            status=SellerCommission.Status.REABERTA,
        )
        j = self._just(date(2026, 6, 25))
        update_day_justification(
            justification=j, user=self.admin, reason='ATESTADO',
        )
        j.refresh_from_db()
        self.assertEqual(j.reason, 'ATESTADO')

    def test_justifications_still_readable_when_locked(self):
        j = self._just(date(2026, 6, 25))
        self._lock(CommissionPeriod.Status.FECHADA)
        # consulta continua funcionando
        by_date, _ = self._statuses()
        self.assertEqual(by_date[date(2026, 6, 25)]['status'], JUSTIFICADO)


class DayStatusApiTest(_Base):
    def _api(self, user):
        c = APIClient()
        c.force_authenticate(user=user)
        return c

    def test_day_status_endpoint(self):  # 20
        self._sale(date(2026, 6, 23))
        self._just(date(2026, 6, 24))
        c = self._api(self.admin)
        url = reverse('api-seller-day-status', args=[self.seller.uuid])
        resp = c.get(url + f'?period={self.period.uuid}')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('summary', resp.data)
        self.assertEqual(resp.data['summary']['lancados'], 1)
        self.assertEqual(resp.data['summary']['justificados'], 1)
        self.assertGreaterEqual(resp.data['summary']['pendentes'], 0)

    def test_other_tenant_404(self):  # 17
        other = Tenant.objects.create(
            company_name='Other', slug='other-flow',
            default_commission_rate=Decimal('0.01'),
        )
        other_seller = Seller.objects.create(
            tenant=other, user=User.objects.create_user(
                username='os_flow', role=User.Role.SELLER, tenant=other,
            ),
            name='OS', phone='55999990888', commission_rate=Decimal('0.01'),
        )
        c = self._api(self.admin)
        url = reverse('api-seller-day-status', args=[other_seller.uuid])
        resp = c.get(url + f'?period={self.period.uuid}')
        self.assertEqual(resp.status_code, 404)

    def test_seller_forbidden_crud(self):  # 18
        c = self._api(self.seller_user)
        resp = c.post(reverse('api-day-justification-list'), {
            'seller': str(self.seller.uuid), 'date': '2026-06-25',
            'reason': 'FALTA',
        }, format='json')
        self.assertEqual(resp.status_code, 403)
        # day-status tambem e gestor-only
        url = reverse('api-seller-day-status', args=[self.seller.uuid])
        self.assertEqual(
            c.get(url + f'?period={self.period.uuid}').status_code, 403,
        )

    def test_create_conflict_returns_409(self):  # 10 (API)
        self._sale(date(2026, 6, 25))
        c = self._api(self.admin)
        resp = c.post(reverse('api-day-justification-list'), {
            'seller': str(self.seller.uuid), 'date': '2026-06-25',
            'reason': 'FALTA',
        }, format='json')
        self.assertEqual(resp.status_code, 409)

    def test_locked_create_returns_409(self):  # 14 (API)
        self.period.status = CommissionPeriod.Status.FECHADA
        self.period.save()
        c = self._api(self.admin)
        resp = c.post(reverse('api-day-justification-list'), {
            'seller': str(self.seller.uuid), 'date': '2026-06-25',
            'reason': 'FALTA',
        }, format='json')
        self.assertEqual(resp.status_code, 409)

    def test_replace_endpoint(self):  # 12 (API)
        j = self._just(date(2026, 6, 25))
        c = self._api(self.admin)
        url = reverse(
            'api-day-justification-replace-with-sale', args=[j.uuid],
        )
        resp = c.post(url, {'amount': 819489}, format='json')
        self.assertEqual(resp.status_code, 201)
        self.assertFalse(
            SellerDayJustification.objects.filter(pk=j.pk).exists(),
        )
        self.assertTrue(
            Sale.objects.filter(seller=self.seller, sale_date=date(2026, 6, 25)).exists(),
        )

    def test_seller_detail_includes_day_status(self):  # LOTE 5 data
        self._sale(date(2026, 6, 23))
        self._just(date(2026, 6, 24))
        c = self._api(self.admin)
        url = '/api/manager/seller/%s/?period=%s' % (
            self.seller.uuid, self.period.uuid,
        )
        resp = c.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertIsNotNone(resp.data.get('day_status'))
        self.assertEqual(resp.data['day_status']['summary']['lancados'], 1)
        self.assertEqual(resp.data['day_status']['summary']['justificados'], 1)


class MobileHomeTest(_Base):
    @patch('app.apps.dashboard.mobile_views.timezone')
    def test_justified_day_not_pending(self, mock_tz):  # 19
        # hoje dentro da competencia; um dia util passado justificado
        mock_tz.localdate.return_value = date(2026, 7, 1)
        justified_day = date(2026, 6, 24)   # quarta-feira
        self._just(justified_day)
        self.client.force_login(self.seller_user)
        resp = self.client.get('/dashboard/mobile/')
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(justified_day, resp.context['missing_past_days'])
