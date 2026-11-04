---
url: https://docs.relaymail.example/v2/webhooks
title: Webhooks
fetched: 2026-10-20
---
# Webhooks

Relaymail posts delivery events (`delivered`, `bounced`, `complained`, `opened`) to your webhook URL as JSON.

If your endpoint does not answer with a 2xx status within 10 seconds, the delivery is retried up to 8 times over
24 hours, with exponential backoff. After the last attempt the event is kept for 7 days and can be replayed from the
dashboard.

Each request carries a `Relay-Signature` header: an HMAC-SHA256 of the body with your webhook secret.
