"""Reporting currency context, shared by UI, chat and briefings."""
from contextlib import contextmanager
from contextvars import ContextVar
import math

_currency = ContextVar("reporting_currency", default="USD")


def currency_code():
    return _currency.get()


def currency_from_frame(df):
    codes = df["currency"].dropna().astype(str).str.upper().unique() if "currency" in df else []
    if len(codes) > 1:
        raise ValueError("Choose one reporting currency before analysis.")
    return str(codes[0]) if len(codes) else "USD"


@contextmanager
def reporting_currency(code):
    token = _currency.set(code)
    try:
        yield
    finally:
        _currency.reset(token)


def money_prefix():
    return {"USD": "$", "SGD": "S$", "AUD": "A$", "CAD": "C$", "HKD": "HK$",
            "EUR": "€", "GBP": "£", "JPY": "¥"}.get(currency_code(), currency_code() + " ")


def money(value, places=0):
    return f"{'-' if value < 0 else ''}{money_prefix()}{abs(value):,.{places}f}"


def percentage(value, places=1):
    if value is None or not math.isfinite(float(value)):
        return "N/A (zero budget)"
    return f"{value:,.{places}f}%"
