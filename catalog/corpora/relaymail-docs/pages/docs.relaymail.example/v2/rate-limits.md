---
url: https://docs.relaymail.example/v2/rate-limits
title: Rate limits
fetched: 2026-10-20
published: 2026-06-02
---
# Rate limits

Each API key may make up to **600 requests per minute** to the v2 API. The limit is per key; every key on an
account has its own allowance.

Requests above the limit get `429 Too Many Requests` with a `Retry-After` header giving the seconds to wait. Short
bursts are smoothed over a sliding one-minute window, so there is no separate per-second limit.

*Last updated 2 June 2026, when the limit was raised for every plan. See the
[changelog](https://docs.relaymail.example/changelog).*
