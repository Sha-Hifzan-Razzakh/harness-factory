# Environments

| Environment | Database host | Notes |
|---|---|---|
| dev | db-dev.internal | local test data, reset nightly |
| staging | db-staging.internal | copy of production, refreshed weekly |
| prod | db-prod.internal | live data |

Each environment reads service/config/base.ini first, then its own file (dev.ini, staging.ini or prod.ini),
which overrides the base values.
