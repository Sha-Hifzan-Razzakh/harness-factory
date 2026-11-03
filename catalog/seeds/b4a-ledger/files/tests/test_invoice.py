import datetime as dt
from decimal import Decimal

from ledger import invoice
from ledger.invoice import Invoice


def test_vat_and_gross():
    inv = Invoice("INV-1", "C-1001", dt.date(2026, 3, 4), "AED", Decimal("200.00"))
    assert inv.vat == Decimal("10.00")
    assert inv.gross == Decimal("210.00")


def test_aed_invoices_need_no_conversion():
    inv = Invoice("INV-2", "C-1001", dt.date(2026, 3, 2), "AED", Decimal("99.99"))
    assert inv.net_aed() == Decimal("99.99")


def test_load_by_date():
    first = invoice.load(dt.date(2026, 2, 2), dt.date(2026, 2, 2))[0]
    assert [first.number, str(first.net), str(first.vat), str(first.gross)] == ['INV-26229', '13655.90', '682.80', '14338.70']


def test_void_invoices_do_not_count():
    inv = Invoice("INV-3", "C-1001", dt.date(2026, 3, 4), "AED", Decimal("5"), "void")
    assert not inv.counts
