---
url: https://docs.relaymail.example/v2/authentication
title: Authentication
fetched: 2026-10-20
---
# Authentication

Send your API key as a bearer token: `Authorization: Bearer rk_live_...`. Keys starting with `rk_test_` send to the
sandbox only and never deliver email.

Create and revoke keys in the dashboard under **Settings > API keys**. Each key has its own
[rate limit](https://docs.relaymail.example/v2/rate-limits).
