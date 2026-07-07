from django import template

register = template.Library()


@register.filter
def brl(value):
    """Formata centavos para Real brasileiro: 8503050 -> R$ 85.030,50"""
    if value is None:
        return 'R$ 0,00'
    try:
        reais = int(value) / 100
        return f'R$ {reais:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
    except (ValueError, TypeError):
        return 'R$ 0,00'
