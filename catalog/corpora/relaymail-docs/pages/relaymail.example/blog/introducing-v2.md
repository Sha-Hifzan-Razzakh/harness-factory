---
url: https://relaymail.example/blog/introducing-v2
title: Introducing Relaymail API v2
fetched: 2026-10-20
published: 2025-11-04
---
# Introducing Relaymail API v2

*4 November 2025, by the Relaymail team*

Today API v2 is generally available. Three things change.

**Bearer tokens.** Keys go in the `Authorization` header, like every other API you use.

**Higher rate limits.** Each API key can now make 300 requests per minute, triple the 100 requests per minute of v1.
If you need more, talk to us: we are already testing higher limits.

**Better webhooks.** Events carry the message's tags and a signature you can verify.

API v1 keeps working until we announce its retirement, with at least four months' notice.
