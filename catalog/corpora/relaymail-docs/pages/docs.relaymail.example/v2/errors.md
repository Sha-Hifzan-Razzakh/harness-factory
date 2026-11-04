---
url: https://docs.relaymail.example/v2/errors
title: Errors
fetched: 2026-10-20
---
# Errors

| Status | Meaning |
|---|---|
| 400 | The request body is not valid; the response says which field. |
| 401 | The API key is missing or revoked. |
| 413 | The message, attachments included, is over 10 MB. |
| 429 | The key went over its [rate limit](https://docs.relaymail.example/v2/rate-limits); wait for `Retry-After`. |
| 503 | Relaymail is briefly unavailable; retry with backoff. |
