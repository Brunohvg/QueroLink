import os
import django

# Defina o caminho para o seu arquivo de configurações
os.environ['DJANGO_SETTINGS_MODULE'] = 'link.settings'  # Substitua 'link.settings' pelo caminho correto do seu arquivo de configurações
django.setup()

# Agora você pode importar o modelo Token e trabalhar normalmente
from rest_framework.authtoken.models import Token
from django.contrib.auth.models import User

# Criar ou obter um usuário (no seu caso 'bruno')
user = User.objects.get(username='bruno')

# Gerar o token para o usuário
token, created = Token.objects.get_or_create(user=user)
print(token.key)
