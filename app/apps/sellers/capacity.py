from dataclasses import dataclass

from django.conf import settings

from app.apps.sellers.models import Seller


@dataclass(frozen=True)
class SellerCapacity:
    plan: str
    limit: int | None
    active: int
    requested: int
    remaining: int | None

    @property
    def exceeded(self):
        return self.remaining is not None and self.requested > self.remaining

    def error_message(self):
        if self.limit is None:
            return ''
        return (
            f'Limite de vendedores do plano {self.plan} excedido. '
            f'Limite: {self.limit}. Ativos: {self.active}. '
            f'Solicitados: {self.requested}. Capacidade restante: {self.remaining}.'
        )


class SellerCapacityExceeded(ValueError):
    def __init__(self, capacity):
        self.capacity = capacity
        super().__init__(capacity.error_message())


def calculate_seller_capacity(tenant, requested=1, active=None):
    limits = getattr(settings, 'PLAN_SELLER_LIMITS', {})
    limit = limits.get(tenant.plan)
    if active is None:
        active = Seller.objects.filter(tenant=tenant, is_active=True).count()
    remaining = None if limit is None else max(limit - active, 0)
    return SellerCapacity(
        plan=tenant.plan,
        limit=limit,
        active=active,
        requested=requested,
        remaining=remaining,
    )


def ensure_seller_capacity(tenant, requested=1, active=None):
    capacity = calculate_seller_capacity(tenant, requested=requested, active=active)
    if capacity.exceeded:
        raise SellerCapacityExceeded(capacity)
    return capacity
