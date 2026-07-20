CSV_FORMULA_PREFIXES = ('=', '+', '-', '@')


def safe_csv_cell(value):
    if isinstance(value, str) and value.lstrip().startswith(CSV_FORMULA_PREFIXES):
        return "'" + value
    return value


def safe_csv_row(values):
    return [safe_csv_cell(value) for value in values]
