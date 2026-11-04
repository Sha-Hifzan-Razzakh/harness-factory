---
url: https://docs.relaymail.example/v2/sending
title: Sending email
fetched: 2026-10-20
---
# Sending email

`POST https://api.relaymail.example/v2/messages` sends one message. The body is JSON with `from`, `to`, `subject`
and either `html` or `text`.

A message may have at most 50 recipients across `to`, `cc` and `bcc`. Attachments may total 10 MB per message.

Every successful request returns `202 Accepted` with the message id; delivery happens asynchronously and is
reported through [webhooks](https://docs.relaymail.example/v2/webhooks).
