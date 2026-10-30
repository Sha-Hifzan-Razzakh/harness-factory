# Dunning

When a scheduled payment fails, the dunning scheduler retries it. Each entry in `retry_days` is a retry,
counted in days after the invoice's due date. After the last retry the `final` action runs.

## Configuration

- `config/dunning/default.toml` holds the defaults for every region.
- A region file (`config/dunning/<region>.toml`) overrides keys from the defaults. Regions without a file
  use the defaults unchanged.
- A list in a region file replaces the default list. Lists are never merged.
- `max_attempts` must equal the number of entries in `retry_days` for the same region. The scheduler refuses
  to start when they differ.
- `final.after_days` must be later than the last retry day.

## Who to ask

Finance owns the retry schedule. Platform owns the scheduler itself.
