import requests
from django.conf import settings

class WhatsappClient:
    """
    Cliente para envio de mensagens via WhatsApp usando a API existente.
    """
    def __init__(self, instance=None, api_key=None):
        self.instance = instance or getattr(settings, 'INSTANCE', '')
        self.api_key = api_key or getattr(settings, 'API_KEY_INSTANCIA', '')

    def send_message(self, number, text):
        number = self._format_number(number)
        payload = {
            "number": number,
            "text": text,
            "delay": 10
        }
        headers = {
            "apikey": self.api_key,
            "Content-Type": "application/json"
        }
    
        url = f"https://api.lojabibelo.com.br/message/sendText/{self.instance}"
        
        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status()
        return response.json()

    def _format_number(self, number):
        number = ''.join(filter(str.isdigit, str(number)))
        if not number.startswith('55'):
            number = '55' + number
        return number
