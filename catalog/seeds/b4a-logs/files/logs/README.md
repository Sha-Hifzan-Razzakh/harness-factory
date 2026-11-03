# Logs

One file per service per day: `logs/<environment>/<service>/<YYYY-MM-DD>.log`. Files are rotated at local
midnight (Asia/Dubai, UTC+04:00) and every timestamp is local time with its offset, so a file holds one local day.

Format: `<timestamp> <host> <level> <event> key=value ...`

- `callback.received` / `callback.accepted` / `callback.rejected`: payment provider callbacks (webhooks). `cb` is
  the callback id; the provider retries a rejected callback with the same `cb` and a higher `attempt`.
- `refund.callback.*`: the refund hook's own callbacks.
- `payout.*`: daily payout batches.
