import datetime as dt
import json
from decimal import Decimal

from ledger.exporters.csv_export import export_csv
from ledger.exporters.json_export import export_invoice
from ledger.invoice import Invoice

INV = Invoice("INV-9", "C-1001", dt.date(2026, 3, 4), "AED", Decimal("200.00"))


def test_json_export():
    out = export_invoice(INV)
    assert out["vat"] == "10.00" and out["gross"] == "210.00" and out["vat_rate"] == 0.05
    json.dumps(out)


def test_csv_export():
    assert export_csv([INV]).splitlines()[1] == "INV-9,C-1001,2026-03-04,AED,200.00,10.00,210.00,issued"
