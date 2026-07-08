import secrets

_LETTERS = 'ABCDEFGHJKMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz'
_DIGITS = '23456789'
_SYMBOLS = '@#%'


def generate_temp_password(length=10):
    """Senha temporaria legivel: sem 0/O/o, 1/l/I - evita erro de leitura/ditado."""
    alphabet = _LETTERS + _DIGITS + _SYMBOLS
    while True:
        pwd = ''.join(secrets.choice(alphabet) for _ in range(length))
        if (
            any(c in _LETTERS for c in pwd)
            and any(c in _DIGITS for c in pwd)
            and any(c in _SYMBOLS for c in pwd)
        ):
            return pwd
