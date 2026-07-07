OFFERED_PLAN_CODES = ('STARTER', 'PRO', 'BUSINESS')


def offered_plan_choices():
    from app.apps.accounts.models import Tenant

    labels = dict(Tenant.Plan.choices)
    return [(code, labels[code]) for code in OFFERED_PLAN_CODES]


def is_offered_plan(plan_code):
    return plan_code in OFFERED_PLAN_CODES
