from django import template

register = template.Library()


@register.filter
def brl(value):
    if value is None:
        return 'R$ 0,00'
    cents = int(value)
    return f"R$ {cents // 100},{cents % 100:02d}"
