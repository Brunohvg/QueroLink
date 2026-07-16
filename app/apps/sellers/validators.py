def normalize_and_validate_cpf(value):
    """Retorna os 11 digitos normalizados ou lanca ValueError com mensagem amigavel."""
    if not value:
        return None
    digits = ''.join(filter(str.isdigit, value))
    if len(digits) != 11:
        raise ValueError('CPF deve ter 11 digitos.')
    if digits == digits[0] * 11:
        raise ValueError('CPF invalido.')
    s1 = sum(int(digits[i]) * (10 - i) for i in range(9))
    d1 = (s1 * 10) % 11
    if d1 == 10:
        d1 = 0
    s2 = sum(int(digits[i]) * (11 - i) for i in range(10))
    d2 = (s2 * 10) % 11
    if d2 == 10:
        d2 = 0
    if int(digits[9]) != d1 or int(digits[10]) != d2:
        raise ValueError('CPF invalido.')
    return digits


def validate_cnpj(value):
    """Retorna os 14 digitos normalizados ou lanca ValueError."""
    digits = ''.join(filter(str.isdigit, value or ''))
    if len(digits) != 14:
        raise ValueError('CNPJ deve ter 14 digitos.')
    if digits == digits[0] * 14:
        raise ValueError('CNPJ invalido.')

    def digit(base, weights):
        total = sum(int(number) * weight for number, weight in zip(base, weights))
        remainder = total % 11
        return 0 if remainder < 2 else 11 - remainder

    first = digit(digits[:12], [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    second = digit(digits[:12] + str(first), [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])
    if digits[-2:] != f'{first}{second}':
        raise ValueError('CNPJ invalido.')
    return digits
