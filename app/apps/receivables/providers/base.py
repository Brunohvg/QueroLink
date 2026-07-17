from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class ProviderStatus(StrEnum):
    CREATING = 'creating'
    PENDING = 'pending'
    PAID = 'paid'
    OVERDUE = 'overdue'
    CANCELED = 'canceled'
    REFUNDED = 'refunded'
    FAILED = 'failed'
    UNKNOWN = 'unknown'


@dataclass(frozen=True)
class ProviderResult:
    provider: str
    order_id: str
    charge_id: str
    status: ProviderStatus
    barcode: str = ''
    url: str = ''


@dataclass(frozen=True)
class ProviderWebhookEvent:
    event_type: str
    order_id: str = ''
    charge_id: str = ''
    status: ProviderStatus = ProviderStatus.UNKNOWN
    paid_amount_cents: int | None = None
    paid_at: datetime | None = None


class ProviderError(Exception):
    code = 'provider_error'


class ProviderTransientError(ProviderError):
    code = 'provider_transient'


class ProviderDefinitiveError(ProviderError):
    code = 'provider_definitive'


class ProviderInconclusiveError(ProviderError):
    code = 'provider_inconclusive'


class BoletoProvider(ABC):
    @abstractmethod
    def create(self, tenant, boleto_data, idempotency_key) -> ProviderResult:
        raise NotImplementedError

    @abstractmethod
    def request_cancel(self, tenant, charge_id) -> ProviderResult:
        raise NotImplementedError

    @abstractmethod
    def retrieve_status(
        self, tenant, *, order_id='', charge_id='', local_code=''
    ) -> ProviderResult:
        raise NotImplementedError

    @abstractmethod
    def parse_webhook(self, payload) -> ProviderWebhookEvent:
        raise NotImplementedError


def get_provider(tenant) -> BoletoProvider:
    from .pagarme import PagarmeBoletoProvider

    return PagarmeBoletoProvider()
