import requests
from decouple import config
class Whatsapp:
    def __init__(self, instance='Bruno', api_key=config("API_KEY_INSTANCIA")):
        self.instance = instance
        self.api_key = api_key
    
    def message_send_text(self, numero_envio, content_text):
        payload = {
            "number": numero_envio,
            "text": content_text,
            "delay": 10
        }
        headers = {
            "apikey": self.api_key,
            "Content-Type": "application/json"
        }
    
        url = f"https://api.lojabibelo.com.br/message/sendText/{self.instance}"
        
        try:
            response = requests.post(url, json=payload, headers=headers)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as http_err:
            print(f"HTTP error occurred: {http_err}")
        except Exception as err:
            print(f"An error occurred: {err}")
        return None

def formatar_numero(numero):
    # Remove caracteres não numéricos
    numero = ''.join(filter(str.isdigit, numero))
    
    # Adiciona o código do país (55) se não estiver presente
    if not numero.startswith('55'):
        numero = '55' + numero
    
    return numero

# Exemplo de uso
if __name__ == '__main__':
    whatsapp_instance = Whatsapp()
    
    numero_formatado = formatar_numero('(31) 97312-1650')
    response = whatsapp_instance.message_send_text(numero_formatado, "Isso é um teste")
    print(response)  # Imprime a resposta da API