import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30


class WhatsAppError(Exception):
    pass


class WhatsappClient:
    """
    Cliente para envio de mensagens via WhatsApp usando a API configurada.
    """

    def __init__(self, instance=None, api_key=None):
        self.instance = instance or getattr(settings, 'WHATSAPP_INSTANCE', '')
        self.api_key = api_key or getattr(settings, 'WHATSAPP_API_KEY', '')
        self.api_base_url = getattr(
            settings, 'WHATSAPP_API_BASE_URL',
            'https://api.lojabibelo.com.br',
        )
        self.timeout = getattr(
            settings, 'WHATSAPP_TIMEOUT', DEFAULT_TIMEOUT,
        )

    def send_message(self, number, text):
        number = self._format_number(number)
        payload = {
            "number": number,
            "text": text,
            "delay": 10,
        }
        headers = {
            "apikey": self.api_key,
            "Content-Type": "application/json",
        }

        url = f"{self.api_base_url}/message/sendText/{self.instance}"

        logger.info("WhatsApp send_message: instance=%s recipient_hash=%s",
                     self.instance, hash(number))

        try:
            response = requests.post(
                url, json=payload, headers=headers,
                timeout=self.timeout,
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.Timeout:
            logger.error("WhatsApp timeout on send_message")
            raise WhatsAppError("Timeout ao comunicar com a API de WhatsApp.")
        except requests.exceptions.RequestException as e:
            logger.error("WhatsApp request error: %s", e)
            raise WhatsAppError(f"Erro na comunicacao com WhatsApp: {e}")
        except ValueError:
            logger.error("WhatsApp response is not valid JSON")
            raise WhatsAppError("Resposta invalida da API WhatsApp.")

    def _format_number(self, number):
        number = ''.join(filter(str.isdigit, str(number)))
        if not number.startswith('55'):
            number = '55' + number
        return number
