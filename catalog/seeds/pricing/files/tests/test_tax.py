from pricing import vat


def test_whole_amounts():
    assert vat(100.0) == 5.0
    assert vat(40.0) == 2.0


def test_halves_round_up():
    assert vat(2.5) == 0.13       # 0.125
    assert vat(0.7) == 0.04       # 0.035
