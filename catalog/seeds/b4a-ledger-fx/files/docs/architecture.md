# Architecture

```
reports ──> invoice ──> fx ──> data/fx_rates.csv
   │           │
   │           └──> money (rounding)
   └──> customers ──> data/customers.csv
aging ──> invoice
pos/receipt, exporters ──> invoice / money
```

All amounts are `Decimal`. Every conversion to AED goes through `fx.convert`, which uses the rate in force on the
invoice date. Treasury publishes rates on Mondays; a rate is valid from its publication day until the next one.
