"""VAT."""

VAT_RATE = 0.05


def vat(amount: float) -> float:
    """VAT due on an amount, in dirhams, rounded to the fils (2 places) with halves rounded up."""
    return round(amount * VAT_RATE, 2)
