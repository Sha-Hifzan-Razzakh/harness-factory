"""Reference solution: branch, switch to it, round with Decimal half-up, run the tests, commit on the branch."""

FIXED = '''"""VAT."""

from decimal import ROUND_HALF_UP, Decimal

VAT_RATE = 0.05


def vat(amount: float) -> float:
    """VAT due on an amount, in dirhams, rounded to the fils (2 places) with halves rounded up."""
    due = Decimal(str(amount)) * Decimal(str(VAT_RATE))
    return float(due.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
'''

PLAN = [{'tool': 'git.branch', 'args': {'name': 'fix/vat-rounding'}},
        {'tool': 'git.checkout', 'args': {'name': 'fix/vat-rounding'}},
        {'tool': 'fs.read_file', 'args': {'path': 'pricing/tax.py'}},
        {'tool': 'fs.write_file', 'args': {'path': 'pricing/tax.py', 'content': FIXED}},
        {'tool': 'code.run_tests', 'args': {'path': 'tests/test_tax.py'}},
        {'tool': 'git.commit', 'args': {'message': 'Round VAT halves up with Decimal'}}]

FINAL = ('vat() now rounds with Decimal and ROUND_HALF_UP (float round() rounded 0.125 down). tests/test_tax.py '
         'passes; the fix is committed on fix/vat-rounding and main is unchanged.')
