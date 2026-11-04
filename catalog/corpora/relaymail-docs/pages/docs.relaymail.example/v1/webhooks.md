---
url: https://docs.relaymail.example/v1/webhooks
title: Webhooks (API v1)
fetched: 2026-10-20
published: 2024-05-14
---
> **Retired.** API v1 stopped accepting requests on 31 March 2026.

# Webhooks

Relaymail posts delivery events to your webhook URL. If your endpoint does not answer with a 2xx status, we retry
the delivery up to 5 times over 1 hour, then drop the event.
