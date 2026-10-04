import csv
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.http import HttpResponse
from django.core.exceptions import ValidationError
from django.core.validators import DecimalValidator

ZERO = Decimal("0")
TWO = Decimal("0.01")


def q2(value):
    return Decimal(value).quantize(TWO, rounding=ROUND_HALF_UP)


def to_decimal(value, default=None):
    """Parse user/JSON input into Decimal. Raises ValueError on garbage."""
    if value is None or value == "":
        if default is not None:
            return default
        raise ValueError("A number is required.")
    try:
        text = str(value).replace(",", "").strip()
        if len(text) > 100:
            raise ValueError("That number is too large.")
        number = Decimal(text)
        if not number.is_finite():
            raise ValueError("Enter a finite number.")
        return number
    except InvalidOperation:
        raise ValueError(f"'{value}' is not a valid number.")


def decimal_input(value, *, label="Number", minimum=None, maximum=None, max_digits=12):
    """Validate a finite value that fits the application's two-decimal fields."""
    number = to_decimal(value)
    try:
        DecimalValidator(max_digits, 2)(number)
    except ValidationError:
        raise ValueError(f"{label} must fit {max_digits - 2} whole digits and at most 2 decimal places.")
    if minimum is not None and number < minimum:
        raise ValueError(f"{label} must be at least {minimum}.")
    if maximum is not None and number > maximum:
        raise ValueError(f"{label} must be at most {maximum}.")
    return number


def optional_id(value):
    """Return a database-safe positive integer, or None for invalid query input."""
    text = str(value or "")
    if not text.isascii() or not text.isdecimal() or len(text) > 19:
        return None
    number = int(text)
    return number if 0 < number <= 9223372036854775807 else None


def qty_str(value):
    s = f"{Decimal(value):f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def money_str(value, symbol=""):
    if value is None:
        return "—"
    v = Decimal(value)
    body = f"{symbol}{abs(v):,.2f}"
    return f"-{body}" if v < 0 else body


def csv_response(filename, header, rows):
    resp = HttpResponse(content_type="text/csv")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    writer = csv.writer(resp)
    def safe_cell(value):
        if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r", "\n")):
            return "'" + value
        return value
    writer.writerow([safe_cell(v) for v in header])
    writer.writerows([safe_cell(v) for v in row] for row in rows)
    return resp
