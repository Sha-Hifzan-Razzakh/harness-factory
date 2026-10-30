from billing.fees import early_discount, late_fee


def test_late_fee_is_two_percent():
    assert late_fee(5000) == 100.0


def test_late_fee_has_a_floor():
    assert late_fee(1000) == 25.0


def test_early_discount():
    assert early_discount(5000) == 100.0
