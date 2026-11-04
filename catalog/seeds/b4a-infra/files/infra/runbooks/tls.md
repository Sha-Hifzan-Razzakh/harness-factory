# TLS verification

`TLS_VERIFY=false` disables certificate checks on a service's upstream calls. It was turned off region-wide in
me-central-1 during the partner CA rollout (November 2025); production was switched back on in env.env since.
Any service that still sets it to false needs a ticket with security.
