from django.shortcuts import render
from .api.pagar_me import PagarMeOrder
from .forms import PagarMeForm
from .models import PagarMeOrder as PagarMeOrderModel
# Create your views here.
def index(request):
    form = PagarMeForm()
    return render(request, 'link/index.html', {'form': form})


def create_link(request):
    if request.method == 'POST':
        form = PagarMeForm(request.POST)
        if form.is_valid():
            total_amount = form.cleaned_data['total_amount']
            max_installments = form.cleaned_data['max_installments']
            customer_name = form.cleaned_data['customer_name']
            whatsapp = form.cleaned_data['whatsapp']

            pagar_me = PagarMeOrder(total_amount, max_installments, customer_name)

            response = pagar_me.create_order()
            return render(request, 'link/index.html', {'form': form, 'response': response})
        else:
            return render(request, 'link/index.html', {'form': form})
    else:
        form = PagarMeForm()
        return render(request, 'link/index.html', {'form': form})

def save_order(request):
    if request.method == 'POST':
        form = PagarMeForm(request.POST)
        if form.is_valid():
            total_amount = form.cleaned_data['total_amount']
            max_installments = form.cleaned_data['max_installments']
            customer_name = form.cleaned_data['customer_name']
            whatsapp = form.cleaned_data['whatsapp']

            order = PagarMeOrderModel(total_amount=total_amount, max_installments=max_installments, customer_name=customer_name, whatsapp=whatsapp)
            order.save()
            return redirect('link:index')
        else: 
            return render(request, 'link/index.html', {'form': form})
    else:
        form = PagarMeForm()
        return render(request, 'link/index.html', {'form': form})
        