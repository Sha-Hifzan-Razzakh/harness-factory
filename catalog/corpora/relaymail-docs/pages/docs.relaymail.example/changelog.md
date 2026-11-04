---
url: https://docs.relaymail.example/changelog
title: Changelog
fetched: 2026-10-20
---
# Changelog

Every change to the Relaymail API, SDKs and dashboard, newest first.

## 2026-10-08: Dashboard

Suppression lists can be exported as CSV from **Settings > Suppressions**.

## 2026-09-22: Webhooks

Webhook deliveries that failed every retry are now kept for 7 days (was 3) and can be replayed from the dashboard.

## 2026-09-03: Sending

The `send_at` field schedules a message up to 72 hours ahead.

## 2026-08-19: SDKs

Python SDK 4.2 adds typed webhook events. Node SDK 6.1 drops Node 18.

## 2026-07-30: Errors

A message over 10 MB now fails with `413` instead of `400`, so clients can tell the two apart.

## 2026-07-14: Dashboard

Per-key usage charts show requests per minute against the key's rate limit.

## 2026-06-02: Rate limits

The per-key rate limit of the v2 API is now **600 requests per minute** on every plan (it was 300). The per-second burst limit is gone: requests are smoothed over a sliding one-minute window.

## 2026-05-12: Webhooks

Webhook retries now follow exponential backoff over 24 hours, up to 8 attempts.

## 2026-04-28: Sending

Recipients per message raised to 50 across `to`, `cc` and `bcc` (was 25).

## 2026-03-31: API v1

API v1 is retired and no longer accepts requests. Its documentation stays online for reference.

## 2026-03-02: Authentication

Keys created before 2025 must be rotated by 31 March 2026; the dashboard lists the affected keys.

## 2026-02-10: SDKs

Go SDK 2.0: context-aware clients, retries on `503` with backoff.

## 2026-01-20: Deliverability

Shared IP pools now warm new senders over 14 days.

## 2025-12-15: Webhooks

`complained` events now include the mailbox provider's feedback-loop id.

## 2025-12-01: API v1

API v1 is deprecated and will be retired on 31 March 2026. Move to v2: see the migration guide.

## 2025-11-04: API v2

API v2 is generally available, with bearer-token authentication and a per-key rate limit of 300 requests per minute, triple v1's limit.

## 2025-10-07: API v2

API v2 public beta. Beta keys are limited to 120 requests per minute.

## 2025-09-16: Dashboard

Two-factor authentication is required for account owners.

## 2025-08-26: Sending

Plain-text bodies are generated from HTML when `text` is omitted.

## 2025-07-29: SDKs

Ruby SDK 3.0 requires Ruby 3.1 or later.

## 2025-06-17: Webhooks

Webhook signatures move to HMAC-SHA256; SHA-1 signatures stop on 1 September 2025.

## 2025-05-20: Deliverability

Dedicated IPs are available on the Scale plan.

## 2025-04-08: API v1

The v1 `X-Relay-Retry` header now also appears on `503` responses.

## 2025-03-11: Dashboard

Message search covers the last 30 days (was 14).

## 2025-02-04: Sending

Tags: up to 5 per message, returned on every webhook event.

## 2025-01-14: API v1

Bounce classification adds `soft` and `hard` subtypes to v1 bounce events.

## 2024-11-19: SDKs

PHP SDK 2.4 supports PHP 8.3.

## 2024-10-01: Dashboard

Team roles: owner, admin, developer and viewer.

## 2024-08-27: Deliverability

DMARC alignment checks run on every new sending domain.

## 2024-07-09: API v1

Attachments may total 10 MB per message (was 7 MB).

## 2024-05-14: API v1

Rate limits are now per API key: 100 requests per minute, bursts of 20 per second.

## 2024-03-26: API v1

Webhooks retry failed deliveries up to 5 times over 1 hour.

## 2024-02-06: Sending

Custom `Reply-To` headers.

## 2024-01-09: Launch

Relaymail opens to everyone. API v1 allows 60 requests per minute per account.
