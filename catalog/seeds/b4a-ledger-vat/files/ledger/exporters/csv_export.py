"""Invoice export as CSV."""

import csv
import io

from ledger.invoice import Invoice


def export_csv(invoices: list[Invoice]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["number", "customer_id", "issued_on", "currency", "net", "vat", "gross", "status"])
    for inv in invoices:
        w.writerow([inv.number, inv.customer_id, inv.issued_on.isoformat(), inv.currency, f"{inv.net:.2f}",
                    f"{inv.vat:.2f}", f"{inv.gross:.2f}", inv.status])
    return buf.getvalue()
