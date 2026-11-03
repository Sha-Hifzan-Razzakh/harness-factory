"""Reference solution: run the failing tests, fix the boundary, run them again, commit."""

FIXED = '''"""Bulk discounts."""

BULK_MIN_ITEMS = 10      # orders with at least this many items get the bulk rate
BULK_RATE = 0.05


def bulk_discount(subtotal: float, items: int) -> float:
    """The discount on an order, in dirhams: BULK_RATE of the subtotal for orders of BULK_MIN_ITEMS items or
    more, otherwise nothing."""
    if items >= BULK_MIN_ITEMS:
        return round(subtotal * BULK_RATE, 2)
    return 0.0
'''

PLAN = [{'tool': 'code.run_tests', 'args': {'path': 'tests/test_discount.py'}},
        {'tool': 'fs.read_file', 'args': {'path': 'pricing/discount.py'}},
        {'tool': 'fs.write_file', 'args': {'path': 'pricing/discount.py', 'content': FIXED}},
        {'tool': 'code.run_tests', 'args': {'path': 'tests/test_discount.py'}},
        {'tool': 'git.commit', 'args': {'message': 'Fix bulk discount for orders of exactly ten items'}}]

FINAL = ('bulk_discount compared items > BULK_MIN_ITEMS, so a 10-item order got no discount; it is now >=. '
         'tests/test_discount.py passes and the fix is committed.')
