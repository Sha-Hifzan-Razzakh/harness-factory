---
url: https://docs.relaymail.example/v1/rate-limits
title: Rate limits (API v1)
fetched: 2026-10-20
published: 2024-05-14
---
> **Retired.** API v1 stopped accepting requests on 31 March 2026. These pages are kept for reference and are not
> updated.

# Rate limits

Every API key may make up to **100 requests per minute**. Requests above the rate limit are rejected with HTTP 429.

The rate limit applies per API key, not per account: an account with three keys can make 300 requests per minute
in total. Bursts of up to 20 requests in one second are allowed as long as the per-minute rate limit is respected.

When you hit the rate limit, wait for the `X-Relay-Retry` number of seconds before sending again.

Questions about rate limits? See the [limits FAQ](https://docs.relaymail.example/v1/limits-faq).
