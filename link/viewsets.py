"""from rest_framework import viewsets
from .models import PagarMePayment
from .serializers import PagarMePaymentSerializer

class PagarMePaymentViewSet(viewsets.ModelViewSet):
    queryset = PagarMePayment.objects.all()
    serializer_class = PagarMePaymentSerializer
"""

from rest_framework import viewsets
from .models import PagarMePayment
from .serializers import PagarMePaymentSerializer
from rest_framework.response import Response
from rest_framework.exceptions import NotFound
from rest_framework import status

class PagarMePaymentViewSet(viewsets.ModelViewSet):
    queryset = PagarMePayment.objects.all()
    serializer_class = PagarMePaymentSerializer

    def get_queryset(self):
        """
        Filtra os pagamentos pela transaction_id, se fornecida.
        """
        queryset = PagarMePayment.objects.all()
        transaction_id = self.request.query_params.get('transaction_id', None)

        if transaction_id is not None:
            queryset = queryset.filter(transaction_id=transaction_id)

        return queryset


"""
from rest_framework.exceptions import NotFound

class PagarMePaymentViewSet(viewsets.ModelViewSet):
    queryset = PagarMePayment.objects.all()
    serializer_class = PagarMePaymentSerializer

    def get_queryset(self):
        queryset = PagarMePayment.objects.all()
        transaction_id = self.request.query_params.get('transaction_id', None)

        if transaction_id is not None:
            queryset = queryset.filter(transaction_id=transaction_id)
            if not queryset.exists():
                raise NotFound(f"Pagamento com transaction_id {transaction_id} não encontrado.")

        return queryset

"""

"""
def get_queryset(self):
    queryset = PagarMePayment.objects.all()
    transaction_id = self.request.query_params.get('transaction_id', None)
    status = self.request.query_params.get('status', None)

    if transaction_id is not None:
        queryset = queryset.filter(transaction_id=transaction_id)

    if status is not None:
        queryset = queryset.filter(status=status)

    return queryset

"""