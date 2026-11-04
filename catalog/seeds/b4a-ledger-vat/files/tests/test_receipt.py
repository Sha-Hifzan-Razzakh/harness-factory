from ledger.pos.receipt import receipt_lines


def test_receipt_shows_the_vat_part():
    lines = receipt_lines([("Pallet wrap", 2, "52.50"), ("Tape", 1, "10.50")])
    assert lines[-2].endswith("115.50")
    assert lines[-1].startswith("VAT 5% included")
    assert lines[-1].endswith("5.50")
