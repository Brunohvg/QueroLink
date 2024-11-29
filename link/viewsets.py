from rest_framework import viewsets
from .models import PagarMePayment
from .serializers import PagarMePaymentSerializer

class PagarMePaymentViewSet(viewsets.ModelViewSet):
    queryset = PagarMePayment.objects.all()
    serializer_class = PagarMePaymentSerializer
