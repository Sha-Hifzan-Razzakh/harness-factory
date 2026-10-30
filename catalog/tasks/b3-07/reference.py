"""Reference solution: only LATE_FEE_RATE changes (the early-payment discount is also 0.02 and stays). At 3% the
floor test's 1000 no longer hits the 25.00 minimum (it is 30.00), so it moves to a balance that still does."""

PLAN = [{"tool": "fs.read_file", "args": {"path": "src/billing/fees.py"}},
        {"tool": "fs.read_file", "args": {"path": "tests/test_fees.py"}},
        {"tool": "fs.write_file",
         "args": {"path": "src/billing/fees.py",
                  "content": '"""Fees and discounts applied when an invoice is settled. Amounts are in AED."""\n'
                             "\n"
                             "LATE_FEE_RATE = 0.03            # share of the open balance charged once an invoice "
                             "is overdue\n"
                             "LATE_FEE_MIN = 25.00            # the late fee is never lower than this\n"
                             "EARLY_PAYMENT_DISCOUNT = 0.02   # share taken off when an invoice is paid within 5 days "
                             "of issue\n"
                             "\n"
                             "\n"
                             "def late_fee(balance: float) -> float:\n"
                             '    """Late fee on an overdue balance, never below LATE_FEE_MIN."""\n'
                             "    return round(max(balance * LATE_FEE_RATE, LATE_FEE_MIN), 2)\n"
                             "\n"
                             "\n"
                             "def early_discount(amount: float) -> float:\n"
                             '    """Discount for paying within 5 days of issue."""\n'
                             "    return round(amount * EARLY_PAYMENT_DISCOUNT, 2)\n"}},
        {"tool": "fs.write_file",
         "args": {"path": "tests/test_fees.py",
                  "content": "from billing.fees import early_discount, late_fee\n"
                             "\n"
                             "\n"
                             "def test_late_fee_is_three_percent():\n"
                             "    assert late_fee(5000) == 150.0\n"
                             "\n"
                             "\n"
                             "def test_late_fee_has_a_floor():\n"
                             "    assert late_fee(500) == 25.0\n"
                             "\n"
                             "\n"
                             "def test_early_discount():\n"
                             "    assert early_discount(5000) == 100.0\n"}}]

FINAL = "LATE_FEE_RATE is 0.03; tests updated (5000 -> 150.00, floor test now uses 500, which still hits the minimum)."
