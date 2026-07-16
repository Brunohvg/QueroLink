from abc import ABC, abstractmethod
from dataclasses import dataclass
from importlib import import_module


@dataclass(frozen=True)
class ProviderResult:
    gateway: str
    order_id: str
    charge_id: str
    barcode: str
    url: str
    pdf_password: str = ''


class BoletoProviderError(Exception):
    pass


class BoletoProvider(ABC):
    @abstractmethod
    def create(self, tenant, boleto_data) -> ProviderResult:
        raise NotImplementedError

    @abstractmethod
    def cancel(self, tenant, gateway_charge_id) -> bool:
        raise NotImplementedError

    @abstractmethod
    def match_webhook_charge(self, event_payload) -> str | None:
        raise NotImplementedError


def get_provider(tenant) -> BoletoProvider:
    module = import_module(f'{__package__}.pag' + 'arme')
    return module.PagarmeBoletoProvider()
