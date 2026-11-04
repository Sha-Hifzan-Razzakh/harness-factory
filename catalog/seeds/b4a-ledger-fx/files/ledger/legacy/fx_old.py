"""Deprecated: the 2024 daily-rate lookup, kept only for the 2024 audit scripts. Nothing imports it."""

import datetime as dt

DAILY_RATES = {"USD": 3.6725, "EUR": 4.02, "GBP": 4.61}


def rate_for(currency: str, day: dt.date) -> float:
    if currency == "AED":
        return 1.0
    return DAILY_RATES[currency]


def to_aed(amount: float, currency: str, day: dt.date) -> float:
    return round(amount * rate_for(currency, day), 2)
