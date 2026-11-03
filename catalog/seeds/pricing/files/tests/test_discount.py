from pricing import bulk_discount


def test_small_orders_get_no_discount():
    assert bulk_discount(200.0, 3) == 0.0
    assert bulk_discount(200.0, 9) == 0.0


def test_bulk_orders_get_five_percent():
    assert bulk_discount(200.0, 12) == 10.0


def test_exactly_ten_items_is_a_bulk_order():
    assert bulk_discount(200.0, 10) == 10.0
