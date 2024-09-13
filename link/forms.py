from django import forms

class PagarMeForm(forms.Form):
    total_amount = forms.DecimalField(label='Valor Total', decimal_places=2, max_digits=10)
    max_installments = forms.IntegerField(label='Máximo de Parcelas')
    customer_name = forms.CharField(label='Nome do Cliente')
    whatsapp = forms.CharField(label='WhatsApp')  