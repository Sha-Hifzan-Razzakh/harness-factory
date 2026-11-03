"""Invoice export as JSON, for the customer portal."""

from ledger.invoice import Invoice


def export_invoice(inv: Invoice) -> dict:
    return {"number": inv.number, "customer_id": inv.customer_id, "issued_on": inv.issued_on.isoformat(),
            "currency": inv.currency, "net": f"{inv.net:.2f}", "vat": f"{inv.vat:.2f}",
            "gross": f"{inv.gross:.2f}", "vat_rate": 0.05, "status": inv.status}
