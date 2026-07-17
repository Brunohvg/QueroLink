from django.test import TestCase

from app.apps.accounts.models import Tenant, tenant_has_feature


class ReceivablesFeatureFlagTests(TestCase):
    def test_feature_is_disabled_by_default(self):
        tenant = Tenant.objects.create(company_name='Default Flag')

        self.assertFalse(tenant.receivables_enabled)
        self.assertFalse(tenant_has_feature(tenant, 'boletos'))

    def test_eligible_plan_with_disabled_flag_has_no_access(self):
        tenant = Tenant.objects.create(
            company_name='Pro Disabled',
            plan=Tenant.Plan.PRO,
            receivables_enabled=False,
        )

        self.assertFalse(tenant_has_feature(tenant, 'boletos'))

    def test_eligible_plan_with_enabled_flag_has_access(self):
        tenant = Tenant.objects.create(
            company_name='Pro Enabled',
            plan=Tenant.Plan.PRO,
            receivables_enabled=True,
        )

        self.assertTrue(tenant_has_feature(tenant, 'boletos'))

    def test_ineligible_plan_with_enabled_flag_has_no_access(self):
        tenant = Tenant.objects.create(
            company_name='Starter Enabled',
            plan=Tenant.Plan.STARTER,
            receivables_enabled=True,
        )

        self.assertFalse(tenant_has_feature(tenant, 'boletos'))

    def test_flag_is_isolated_by_tenant(self):
        enabled = Tenant.objects.create(
            company_name='Enabled Tenant',
            plan=Tenant.Plan.BUSINESS,
            receivables_enabled=True,
        )
        disabled = Tenant.objects.create(
            company_name='Disabled Tenant',
            plan=Tenant.Plan.BUSINESS,
            receivables_enabled=False,
        )

        self.assertTrue(tenant_has_feature(enabled, 'boletos'))
        self.assertFalse(tenant_has_feature(disabled, 'boletos'))
