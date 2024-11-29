from rest_framework import serializers
from .models import PagarMePayment

class PagarMePaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = PagarMePayment
        fields = '__all__'  # Inclui todos os campos do modelo no JSON de resposta
