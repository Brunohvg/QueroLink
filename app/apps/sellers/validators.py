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
