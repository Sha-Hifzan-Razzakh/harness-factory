"""Point-of-sale receipts. Shelf prices include VAT; the receipt shows the VAT part of the total."""

from ledger.money import D, fmt, round_money

VAT_PERCENT = 5
WIDTH = 40


def receipt_lines(items: list[tuple[str, int, object]]) -> list[str]:
    """items: (name, quantity, unit price including VAT)."""
    lines, total = [], D(0)
    for name, qty, price in items:
        amount = round_money(D(price) * qty)
        total += amount
        left = f"{qty} x {name}"[: WIDTH - 12]
        lines.append(f"{left:<{WIDTH - 12}}{fmt(amount):>12}")
    vat_part = round_money(total * VAT_PERCENT / (100 + VAT_PERCENT))
    lines.append("-" * WIDTH)
    lines.append(f"{'Total':<{WIDTH - 12}}{fmt(total):>12}")
    lines.append(f"{f"VAT {VAT_PERCENT}% included":<{WIDTH - 12}}{fmt(vat_part):>12}")
    return lines
