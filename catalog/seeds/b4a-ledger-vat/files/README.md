# ledger

Accounts receivable for Gulf Distribution Co.: invoices, VAT, FX conversion to AED, aging and revenue reports.

- `ledger/invoice.py`: invoices from `ledger/data/invoices.csv`
- `ledger/fx.py`: weekly treasury rates from `ledger/data/fx_rates.csv`
- `ledger/reports.py`: revenue by region, customer and month (AED, net of VAT)
- `ledger/aging.py`: open balances by currency and bucket
- `ledger/pos/receipt.py`: point-of-sale receipts
- `ledger/exporters/`: CSV and JSON export
- `ledger/legacy/`: 2024 code kept for the audit scripts; not used by the package

Run the tests with `python -m pytest tests`. Releases are cut from `main` on a `release/<version>` branch; see
`docs/releases.csv` for past cuts and `docs/merged_prs.csv` for merged pull requests.
