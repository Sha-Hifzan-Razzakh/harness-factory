"""Deprecated: VAT before the ledger package (2024). Kept for the audit scripts; nothing imports it."""

VAT = 0.05


def vat(net: float) -> float:
    return round(net * VAT, 2)
