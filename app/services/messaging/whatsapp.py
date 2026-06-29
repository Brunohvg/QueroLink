import logging

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30
DEFAULT_TYPING_DELAY = 1200


class WhatsAppError(Exception):
    def __init__(self, message, code=None, http_status=None):
        super().__init__(message)
        self.code = code
        self.http_status = http_status


class InstanceNotFoundError(WhatsAppError):
    pass


class AuthenticationError(WhatsAppError):
    pass


class MessageSendError(WhatsAppError):
    pass


class ConnectionError(WhatsAppError):
    pass


class WhatsappClient:
    """
    Cliente para envio de mensagens via Evolution API (WhatsApp).
    Docs: https://github.com/evolution-foundation/evolution-api

    Compatível com Evolution API auto-hospedada ou SaaS.
    Autenticacao via header apikey (global ou instance-scoped).
    """

    def __init__(self, instance=None, api_key=None, webhook_url=None):
        self.instance = instance or getattr(settings, 'WHATSAPP_INSTANCE', '')
        self.api_key = api_key or getattr(settings, 'WHATSAPP_API_KEY', '')
        self.api_base_url = getattr(
            settings, 'WHATSAPP_API_BASE_URL',
            'https://api.lojabibelo.com.br',
        ).rstrip('/')
        self.timeout = getattr(
            settings, 'WHATSAPP_TIMEOUT', DEFAULT_TIMEOUT,
        )
        self.typing_delay = getattr(
            settings, 'WHATSAPP_TYPING_DELAY', DEFAULT_TYPING_DELAY,
        )
        self.webhook_url = webhook_url

    def send_message(self, number, text):
        number = self._format_number(number)
        payload = {
            "number": number,
            "text": text,
            "delay": self.typing_delay,
        }

        headers = {
            "apikey": self.api_key,
            "Content-Type": "application/json",
        }

        url = f"{self.api_base_url}/message/sendText/{self.instance}"

        logger.info(
            "Evolution API sendText: instance=%s recipient_hash=%s len=%d",
            self.instance, abs(hash(number)), len(text),
        )

        try:
            response = requests.post(
                url, json=payload, headers=headers,
                timeout=self.timeout,
            )
        except requests.exceptions.Timeout:
            logger.error("Evolution API timeout: instance=%s", self.instance)
            raise WhatsAppError(
                "Timeout ao comunicar com a Evolution API. "
                "Verifique a conectividade do servidor.",
            )
        except requests.exceptions.ConnectionError:
            logger.error(
                "Evolution API connection error: instance=%s url=%s",
                self.instance, url,
            )
            raise WhatsAppError(
                "Nao foi possivel conectar a Evolution API. "
                "Verifique a URL e se o servidor esta online.",
            )
        except requests.exceptions.RequestException as e:
            logger.error("Evolution API request error: %s", e)
            raise WhatsAppError(
                f"Erro na comunicacao com Evolution API: {e}",
            )

        if response.status_code == 401:
            logger.error("Evolution API unauthorized: instance=%s", self.instance)
            raise AuthenticationError(
                "Chave de API da Evolution API invalida. "
                "Verifique a configuracao de WHATSAPP_API_KEY.",
                http_status=401,
            )
        if response.status_code == 404:
            logger.error("Evolution API instance not found: %s", self.instance)
            raise InstanceNotFoundError(
                f"Instancia WhatsApp '{self.instance}' nao encontrada. "
                "Verifique a configuracao de WHATSAPP_INSTANCE.",
                http_status=404,
            )
        if response.status_code == 403:
            logger.error("Evolution API forbidden: instance=%s", self.instance)
            raise AuthenticationError(
                "Acesso negado pela Evolution API. "
                "Verifique as permissoes da chave de API.",
                http_status=403,
            )

        response.raise_for_status()

        try:
            data = response.json()
        except ValueError:
            logger.error("Evolution API response is not valid JSON")
            raise WhatsAppError("Resposta invalida da Evolution API.")

        if isinstance(data, dict) and data.get('error'):
            error_msg = data['error'].get('message', str(data['error']))
            logger.error("Evolution API error response: %s", error_msg)
            raise MessageSendError(
                f"Evolution API: {error_msg}",
                code=data['error'].get('code'),
            )

        msg_id = data.get('key', {}) if isinstance(data, dict) else {}
        logger.info(
            "Evolution API message sent: instance=%s message_id=%s",
            self.instance,
            msg_id.get('id', 'unknown') if isinstance(msg_id, dict) else 'unknown',
        )

        return data

    def instance_exists(self):
        try:
            dados = self._get(f"/instance/connectionState/{self.instance}")
            return isinstance(dados, dict)
        except InstanceNotFoundError:
            return False

    def _parse_qrcode_response(self, dados, is_new=False):
        if not isinstance(dados, dict):
            raise WhatsAppError("Resposta inesperada da Evolution API.")

        # Create response: {"instance":..., "qrcode": {"base64":...}, "hash":...}
        # Connect response: {"base64":..., "code":..., "pairingCode":...} (top-level)
        qrcode = dados.get('qrcode', {}) or {}
        if not isinstance(qrcode, dict) or not qrcode.get('base64'):
            qrcode = dados

        instance_key = (
            dados.get('hash')
            or dados.get('instance', {}).get('token')
        )
        inst = dados.get('instance', {}) or {}
        state = inst.get('state') or inst.get('connectionState') or 'connecting'
        return {
            'qrcode_base64': qrcode.get('base64') if isinstance(qrcode, dict) else None,
            'pairing_code': qrcode.get('pairingCode') if isinstance(qrcode, dict) else None,
            'code': qrcode.get('code') if isinstance(qrcode, dict) else None,
            'instance_api_key': instance_key,
            'state': state,
            'instance_created': is_new,
            'raw': dados,
        }

    def create_or_get_qrcode(self):
        # 1. Check if instance exists
        try:
            state = self.get_connection_state()
            if state['connected']:
                raise WhatsAppError(
                    f"Instancia '{self.instance}' ja esta conectada. "
                    "Nao e necessario gerar QR Code."
                )
            # Exists but disconnected → reconnect
            dados = self._get(f"/instance/connect/{self.instance}")
            return self._parse_qrcode_response(dados, is_new=False)
        except InstanceNotFoundError:
            pass

        # 2. Instance doesn't exist → create new
        payload = {
            "instanceName": self.instance,
            "qrcode": True,
            "integration": "WHATSAPP-BAILEYS",
        }
        if self.webhook_url:
            payload["webhook"] = {
                "url": self.webhook_url,
                "events": ["CONNECTION_UPDATE", "QRCODE_UPDATE"],
            }

        dados = self._post("/instance/create", payload)
        return self._parse_qrcode_response(dados, is_new=True)

    def get_connection_state(self):
        dados = self._get(f"/instance/connectionState/{self.instance}")
        if not isinstance(dados, dict):
            raise WhatsAppError("Resposta inesperada ao consultar instancia.")
        inst = dados.get('instance', {}) or {}
        state = inst.get('state') or inst.get('connectionState') or 'unknown'
        return {
            'connected': state == 'open',
            'state': state,
            'instance_name': inst.get('instanceName'),
            'owner': inst.get('owner'),
            'raw': dados,
        }

    def disconnect(self):
        self._delete(f"/instance/logout/{self.instance}")

    def delete_instance(self):
        self._delete(f"/instance/delete/{self.instance}")

    def _request(self, method, path, **kwargs):
        url = f"{self.api_base_url}{path}"
        headers = {"apikey": self.api_key}
        if 'json' in kwargs:
            headers["Content-Type"] = "application/json"
        try:
            response = requests.request(method, url, headers=headers, timeout=self.timeout, **kwargs)
        except requests.exceptions.RequestException as e:
            raise ConnectionError(f"Erro na requisicao: {e}")
        return self._handle_response(response)

    def _post(self, path, body):
        return self._request("POST", path, json=body)

    def _get(self, path):
        return self._request("GET", path)

    def _delete(self, path):
        return self._request("DELETE", path)

    def _handle_response(self, response):
        if response.status_code == 404:
            raise InstanceNotFoundError(
                f"Instancia '{self.instance}' nao encontrada.",
                http_status=404,
            )
        if response.status_code == 401:
            raise AuthenticationError(
                "Chave de API invalida.", http_status=401,
            )
        if response.status_code == 403:
            raise AuthenticationError(
                "Acesso negado pela Evolution API.", http_status=403,
            )
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError:
            raise WhatsAppError("Resposta invalida da Evolution API.")
        if not isinstance(data, dict):
            logger.warning("Evolution API returned non-dict response: %s", type(data).__name__)
            raise WhatsAppError("Resposta inesperada da Evolution API.")
        if data.get('error'):
            raise ConnectionError(
                data['error'].get('message', 'Erro desconhecido'),
                code=data['error'].get('code'),
            )
        return data

    def _format_number(self, number):
        cleaned = ''.join(filter(str.isdigit, str(number)))

        if cleaned.startswith('55'):
            if len(cleaned) in (12, 13):
                pass
        else:
            if len(cleaned) in (10, 11):
                cleaned = '55' + cleaned
            elif len(cleaned) == 12:
                cleaned = '55' + cleaned

        if len(cleaned) < 12:
            logger.warning(
                "WhatsApp number may be invalid: original=%s cleaned=%s (%d digits)",
                number, cleaned, len(cleaned),
            )

        return cleaned
