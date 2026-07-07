import re


def clean_cnpj(cnpj):
    return re.sub(r'[^0-9]', '', cnpj)


def validate_cnpj(cnpj):
    cnpj = clean_cnpj(cnpj)
    if len(cnpj) != 14:
        return False
    if cnpj == cnpj[0] * 14:
        return False

    def _calc_digit(prefix, weights):
        total = sum(int(prefix[i]) * weights[i] for i in range(len(weights)))
        remainder = total % 11
        return 0 if remainder < 2 else 11 - remainder

    w1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    w2 = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]

    d1 = _calc_digit(cnpj[:12], w1)
    d2 = _calc_digit(cnpj[:13], w2)

    return int(cnpj[12]) == d1 and int(cnpj[13]) == d2


def clean_phone(phone):
    return re.sub(r'[^0-9]', '', phone)


def validate_phone_br(phone):
    digits = clean_phone(phone)

    if not digits.isdigit():
        return False

    if len(digits) == 10:
        ddd = digits[:2]
        number = digits[2:]
        if not (11 <= int(ddd) <= 99):
            return False
        if len(number) != 8:
            return False
        return True

    elif len(digits) == 11:
        ddd = digits[:2]
        number = digits[2:]
        if not (11 <= int(ddd) <= 99):
            return False
        if number[0] != '9':
            return False
        if len(number) != 9:
            return False
        return True

    return False



