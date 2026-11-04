# Database failover

The ledger and reporting databases ran on `pg-legacy-2.internal` until October 2026. During the retirement window, if a
service still reports connection errors to `pg-legacy-2.internal`, check its effective `DB_HOST` / `DATABASE_URL` (see
infra/README.md for the layer order) and point it at `pg-main-1.internal`.

Replica: `pg-main-1-ro.internal`. Do not use `pg-legacy-20.internal`: it is the analytics warehouse, not a
failover target.
