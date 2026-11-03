"""Batch 4 (author A): large file-tree seeds.

- b4a-infra (b4-06, b4-08): a layered deployment config tree, 3 regions x 3 environments x 14 services, with stale
  backups (*.bak, *.orig), an archive copy, a decommissioned prod-2025 tree and runbooks. Only `*.env` files under
  regions/<region>/<prod|staging|dev>/ are read by the deployer, in layer order, last assignment wins.
- b4a-logs (b4-07): two months of payment-gateway, refund-hook and payout logs for production, one file per local
  (Asia/Dubai) day, plus a month of staging logs, with look-alike merchants, retries and other rejection reasons.

Deterministic: random.Random with fixed seeds, fixed dates.
"""

import datetime as dt
import random
import sys
from pathlib import Path

# ------------------------------------------------------------------ infra

REGIONS = ("me-central-1", "eu-west-1", "ap-south-1")
ENVS = ("prod", "staging", "dev")
SERVICES = ("api-gateway", "audit-trail", "auth-proxy", "billing-api", "customer-portal", "fx-feed",
            "inventory-svc", "ledger-sync", "notification-hub", "order-router", "partner-gateway", "report-mailer",
            "search-indexer", "warehouse-bridge")
LEGACY, MAIN = "pg-legacy-2.internal", "pg-main-1.internal"

INFRA_README = """# Deployment config

The deployer builds each service's environment from these layers, in this order; a later layer overrides an
earlier one:

1. `defaults/service.env`
2. `regions/<region>/region.env`
3. `regions/<region>/<environment>/env.env`
4. `regions/<region>/<environment>/services/<service>.env`

`<environment>` is one of `prod`, `staging` or `dev`. Within a file the last assignment of a key wins. Lines that
start with `#` are comments. The deployer reads only files ending in `.env` at those paths: editor backups
(`*.bak`, `*.orig`), `archive/` and any other folder under a region (such as the decommissioned `prod-2025/`) are
never deployed.

Keys are `KEY=value` with no spaces around `=`. Boolean values are `true` or `false`.
"""

DEFAULTS = """# Defaults for every service in every region and environment
LOG_LEVEL=info
WORKERS=2
TIMEOUT_MS=5000
RETRY_MAX=3
TLS_VERIFY=true
METRICS_ENABLED=true
DB_POOL_SIZE=10
"""

RUNBOOK_DB = f"""# Database failover

The ledger and reporting databases ran on `{LEGACY}` until October 2026. During the retirement window, if a
service still reports connection errors to `{LEGACY}`, check its effective `DB_HOST` / `DATABASE_URL` (see
infra/README.md for the layer order) and point it at `{MAIN}`.

Replica: `pg-main-1-ro.internal`. Do not use `pg-legacy-20.internal`: it is the analytics warehouse, not a
failover target.
"""

RUNBOOK_TLS = """# TLS verification

`TLS_VERIFY=false` disables certificate checks on a service's upstream calls. It was turned off region-wide in
me-central-1 during the partner CA rollout (November 2025); production was switched back on in env.env since.
Any service that still sets it to false needs a ticket with security.
"""

RUNBOOK_DEPLOY = """# Deploying

1. Change the right layer (see infra/README.md); never edit archive/ or backups.
2. Open a pull request; CI renders every service's environment and diffs it.
3. Merge; the deployer rolls out region by region.
"""


def _port(svc: str) -> int:
    return 8000 + 10 * SERVICES.index(svc)


def service_lines(region: str, env: str, svc: str) -> list[str]:
    rng = random.Random(f"{region}/{env}/{svc}")
    lines = [f"# {svc}: {region} {env}", f"SERVICE_NAME={svc}", f"PORT={_port(svc)}",
             f"WORKERS={rng.choice((2, 4, 6, 8)) if env == 'prod' else 1}"]
    if env == "dev":
        lines.append("LOG_LEVEL=debug")
    lines += [f"DB_NAME={svc.replace('-', '_')}",
              f"DB_POOL_SIZE={rng.choice((10, 20, 30)) if env == 'prod' else 5}",
              f"CACHE_URL=redis://cache-{rng.randint(1, 3)}.{region}.internal:6379/{rng.randint(0, 7)}",
              f"UPSTREAM_URL=https://{rng.choice(SERVICES)}.{region}.internal:{_port(rng.choice(SERVICES))}",
              f"TIMEOUT_MS={rng.choice((2000, 3000, 5000, 8000))}",
              f"RETRY_MAX={rng.choice((2, 3, 5))}"]
    if rng.random() < 0.4:
        lines.append(f"FEATURE_{rng.choice(('BATCHING', 'ASYNC_EXPORT', 'NEW_ROUTING', 'SHADOW_WRITES'))}="
                     f"{rng.choice(('true', 'false'))}")
    lines.append(f"METRICS_PORT={_port(svc) + 1000}")
    return lines


def env_lines(region: str, env: str) -> list[str]:
    db = {"prod": MAIN, "staging": "pg-stg-1.internal", "dev": "pg-dev-1.internal"}[env]
    lines = [f"# {region} {env}", f"ENVIRONMENT={env}", f"DB_HOST={db}"]
    if env == "prod":
        lines.append("LOG_LEVEL=warn")
    return lines


def region_lines(region: str) -> list[str]:
    return [f"# {region}", f"REGION={region}", f"OTEL_ENDPOINT=https://otel.{region}.internal:4318",
            f"SECRETS_PATH=/run/secrets/{region}"]


def _set(lines: list[str], key: str, value: str, after: str | None = None) -> None:
    """Replace key's line, or add it (after the line starting with `after`, else at the end)."""
    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            return
    if after is not None:
        i = next(k for k, line in enumerate(lines) if line.startswith(after))
        lines.insert(i + 1, f"{key}={value}")
    else:
        lines.append(f"{key}={value}")


def infra_tree() -> dict[str, str]:
    files: dict[str, list[str]] = {}
    for region in REGIONS:
        files[f"regions/{region}/region.env"] = region_lines(region)
        for env in ENVS:
            files[f"regions/{region}/{env}/env.env"] = env_lines(region, env)
            for svc in SERVICES:
                files[f"regions/{region}/{env}/services/{svc}.env"] = service_lines(region, env, svc)

    def f(region, env, svc):
        return files[f"regions/{region}/{env}/services/{svc}.env"]

    # ---- TLS (b4-06): effective false in me-central-1 prod for ledger-sync, partner-gateway, report-mailer
    files["regions/me-central-1/region.env"] += ["# partner CA rollout (2025-11): verification off region-wide",
                                                 "TLS_VERIFY=false"]
    files["regions/me-central-1/prod/env.env"] += ["# CA rollout finished: verification back on in production",
                                                   "TLS_VERIFY=true"]
    _set(f("me-central-1", "prod", "partner-gateway"), "TLS_VERIFY", "false", after="UPSTREAM_URL")
    f("me-central-1", "prod", "partner-gateway").insert(-1, "# partner's sandbox CA is self-signed (SEC-2291)")
    ls = f("me-central-1", "prod", "ledger-sync")
    _set(ls, "TLS_VERIFY", "true", after="UPSTREAM_URL")
    ls += ["# bank SFTP bridge has an expired intermediate (SEC-2314)", "TLS_VERIFY=false"]
    _set(f("me-central-1", "prod", "report-mailer"), "TLS_VERIFY", "false", after="TIMEOUT_MS")
    fx = f("me-central-1", "prod", "fx-feed")
    _set(fx, "TLS_VERIFY", "false", after="UPSTREAM_URL")
    fx += ["# rate provider fixed its chain (SEC-2302)", "TLS_VERIFY=true"]
    wb = f("me-central-1", "prod", "warehouse-bridge")
    wb.insert(-1, "# TLS_VERIFY=false   # re-enabled after the CA fix")
    _set(f("me-central-1", "staging", "search-indexer"), "TLS_VERIFY", "false", after="TIMEOUT_MS")
    _set(f("eu-west-1", "prod", "customer-portal"), "TLS_VERIFY", "false", after="TIMEOUT_MS")
    _set(f("me-central-1", "dev", "api-gateway"), "TLS_VERIFY", "false")

    # ---- DB host (b4-08): active production settings that name pg-legacy-2
    _set(files["regions/eu-west-1/prod/env.env"], "DB_HOST", LEGACY)
    _set(ls, "DATABASE_URL", f"postgresql://ledger_sync@{LEGACY}:5432/ledger", after="DB_POOL_SIZE")
    _set(f("me-central-1", "prod", "report-mailer"), "DB_HOST", LEGACY, after="DB_NAME")
    _set(f("ap-south-1", "prod", "billing-api"), "DB_HOST_RO", LEGACY, after="DB_POOL_SIZE")
    _set(f("ap-south-1", "prod", "fx-feed"), "DB_HOST", LEGACY, after="DB_NAME")
    # look-alikes and settings the task must leave alone
    _set(f("me-central-1", "prod", "audit-trail"), "DB_HOST", "pg-legacy-20.internal", after="DB_NAME")
    orr = f("me-central-1", "prod", "order-router")
    orr.insert(orr.index(next(x for x in orr if x.startswith("DB_NAME="))) + 1,
               f"# DB_HOST={LEGACY}   # moved to {MAIN} in May")
    _set(orr, "DB_HOST", MAIN, after="# DB_HOST=")
    _set(files["regions/eu-west-1/staging/env.env"], "DB_HOST", LEGACY)
    _set(f("me-central-1", "staging", "report-mailer"), "DB_HOST", LEGACY, after="DB_NAME")
    _set(f("eu-west-1", "dev", "billing-api"), "DB_HOST_RO", LEGACY, after="DB_POOL_SIZE")

    text = {path: "\n".join(lines) + "\n" for path, lines in files.items()}

    # ---- not deployed: backups, archive, decommissioned tree
    def stale(path: str, edits: dict[str, str]) -> str:
        lines = text[path].splitlines()
        for k, v in edits.items():
            _set(lines, k, v)
        return "\n".join(lines) + "\n"

    mc = "regions/me-central-1/prod/services"
    extra = {
        f"{mc}/auth-proxy.env.bak": stale(f"{mc}/auth-proxy.env", {"TLS_VERIFY": "false"}),
        f"{mc}/report-mailer.env.bak": stale(f"{mc}/report-mailer.env", {"WORKERS": "2"}),
        f"{mc}/fx-feed.env.orig": stale(f"{mc}/fx-feed.env", {"TIMEOUT_MS": "9000"}),
        "regions/ap-south-1/prod/services/billing-api.env.orig":
            stale("regions/ap-south-1/prod/services/billing-api.env", {"WORKERS": "4"}),
        "regions/eu-west-1/prod/env.env.bak": stale("regions/eu-west-1/prod/env.env", {"LOG_LEVEL": "info"}),
        "regions/ap-south-1/staging/services/order-router.env.bak":
            stale("regions/ap-south-1/staging/services/order-router.env", {"RETRY_MAX": "9"}),
    }
    for svc in SERVICES:                                    # the June snapshot of me-central-1 production
        src = f"{mc}/{svc}.env"
        edits = {"WORKERS": "2"}
        if svc in ("order-router", "auth-proxy", "inventory-svc"):
            edits["TLS_VERIFY"] = "false"
        if svc in ("order-router", "billing-api", "notification-hub"):
            edits["DB_HOST"] = LEGACY
        extra[f"archive/2026-06/{src}"] = stale(src, edits)
    extra["archive/2026-06/regions/me-central-1/prod/env.env"] = text["regions/me-central-1/prod/env.env"]
    extra["archive/README.md"] = "Snapshots of production config before changes. Not deployed.\n"
    for svc in ("ledger-sync", "order-router", "report-mailer", "billing-api"):
        src = f"{mc}/{svc}.env"
        edits = {"WORKERS": "1"}
        if svc == "ledger-sync":
            edits["DATABASE_URL"] = f"postgresql://ledger_sync@{LEGACY}:5432/ledger_2025"
        if svc == "order-router":
            edits["TLS_VERIFY"] = "false"
            edits["DB_HOST"] = LEGACY
        extra[f"regions/me-central-1/prod-2025/services/{svc}.env"] = stale(src, edits)
    extra["regions/me-central-1/prod-2025/README.md"] = ("Decommissioned 2025 production stack, kept until the "
                                                         "audit closes. Not deployed.\n")
    return {"infra/README.md": INFRA_README, "infra/defaults/service.env": DEFAULTS,
            "infra/runbooks/database-failover.md": RUNBOOK_DB, "infra/runbooks/tls.md": RUNBOOK_TLS,
            "infra/runbooks/deploying.md": RUNBOOK_DEPLOY,
            **{f"infra/{p}": t for p, t in sorted({**text, **extra}.items())}}


def effective(files: dict[str, str], region: str, env: str, svc: str) -> dict[str, str]:
    """A service's environment, by the README's rules."""
    out: dict[str, str] = {}
    for layer in ("infra/defaults/service.env", f"infra/regions/{region}/region.env",
                  f"infra/regions/{region}/{env}/env.env", f"infra/regions/{region}/{env}/services/{svc}.env"):
        for line in files.get(layer, "").splitlines():
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k] = v
    return out


# ------------------------------------------------------------------ logs

TZ = dt.timezone(dt.timedelta(hours=4))
TARGET = "M-4471"
MERCHANTS = [f"M-{n}" for n in (1032, 1187, 2045, 2290, 2716, 3120, 3388, 3904, 4417, 4471, 44710, 5021, 5560,
                                6013, 6248, 7102, 7735, 8090, 8461, 9127)]
HOSTS = ("gw-1", "gw-2", "gw-3")
EVENTS = ("payment.captured", "payment.authorized", "payment.failed", "payment.refunded")
START, END = dt.datetime(2026, 9, 1, tzinfo=TZ), dt.datetime(2026, 11, 1, tzinfo=TZ)
WINDOW = (dt.date(2026, 10, 12), dt.date(2026, 10, 18))       # UTC days the b4-07 report covers

# Hand-placed M-4471 signature rejections near the window, (UTC time, attempts). Each is one callback; attempts
# > 1 are provider retries of the same callback, a few minutes apart, all rejected. The ones just before or after
# a UTC midnight sit in the neighbouring local-date file (UTC+4).
TARGET_REJECTIONS = [
    ("2026-10-11T21:30:00", 1), ("2026-10-11T23:30:00", 2),             # local Oct 12, UTC Oct 11
    ("2026-10-12T06:12:00", 3), ("2026-10-12T13:40:00", 1), ("2026-10-12T22:10:00", 2),   # last: local Oct 13
    ("2026-10-14T09:05:00", 1), ("2026-10-14T17:45:00", 3),
    ("2026-10-15T02:20:00", 1), ("2026-10-15T08:00:00", 2), ("2026-10-15T11:31:00", 1), ("2026-10-15T19:50:00", 1),
    ("2026-10-16T15:15:00", 2),
    ("2026-10-17T20:30:00", 1),                                          # local Oct 18
    ("2026-10-18T10:10:00", 1), ("2026-10-18T23:40:00", 1),              # last: local Oct 19
    ("2026-10-19T00:30:00", 2),                                          # UTC Oct 19, outside
]
# Same-window look-alikes: other merchants, other reasons, other services, staging
LOOKALIKES = [
    ("prod/payments-gw", "M-44710", "signature_invalid", "2026-10-13T10:00:00", 1),
    ("prod/payments-gw", "M-44710", "signature_invalid", "2026-10-16T07:20:00", 2),
    ("prod/payments-gw", "M-4417", "signature_invalid", "2026-10-14T12:00:00", 1),
    ("prod/payments-gw", TARGET, "timestamp_skew", "2026-10-13T08:30:00", 1),
    ("prod/payments-gw", TARGET, "timestamp_skew", "2026-10-17T14:10:00", 2),
    ("prod/payments-gw", TARGET, "unknown_key", "2026-10-16T09:00:00", 1),
    ("prod/refunds-hook", TARGET, "signature_invalid", "2026-10-13T11:11:00", 1),
    ("prod/refunds-hook", TARGET, "signature_invalid", "2026-10-17T16:40:00", 1),
    ("staging/payments-gw", TARGET, "signature_invalid", "2026-10-13T09:00:00", 3),
    ("staging/payments-gw", TARGET, "signature_invalid", "2026-10-17T09:30:00", 1),
]

LOGS_README = """# Logs

One file per service per day: `logs/<environment>/<service>/<YYYY-MM-DD>.log`. Files are rotated at local
midnight (Asia/Dubai, UTC+04:00) and every timestamp is local time with its offset, so a file holds one local day.

Format: `<timestamp> <host> <level> <event> key=value ...`

- `callback.received` / `callback.accepted` / `callback.rejected`: payment provider callbacks (webhooks). `cb` is
  the callback id; the provider retries a rejected callback with the same `cb` and a higher `attempt`.
- `refund.callback.*`: the refund hook's own callbacks.
- `payout.*`: daily payout batches.
"""


def _cb(rng: random.Random) -> str:
    return "cb_" + "".join(rng.choice("0123456789abcdef") for _ in range(10))


def _line(t: dt.datetime, host: str, level: str, name: str, **kv) -> tuple[dt.datetime, str]:
    stamp = t.astimezone(TZ).isoformat(timespec="seconds")
    return t, f"{stamp} {host} {level:<5} {name} " + " ".join(f"{k}={v}" for k, v in kv.items())


def _rejection(rng, service: str, merchant: str, reason: str, utc: str, attempts: int, cb: str | None = None):
    t = dt.datetime.fromisoformat(utc).replace(tzinfo=dt.UTC)
    cb = cb or _cb(rng)
    host = rng.choice(HOSTS)
    prefix = "refund.callback" if service.endswith("refunds-hook") else "callback"
    out = []
    for a in range(1, attempts + 1):
        ta = t + dt.timedelta(minutes=7 * (a - 1) * a)              # 0, 14, 42 minutes later
        out.append(_line(ta, host, "INFO", f"{prefix}.received", merchant=merchant, cb=cb, attempt=a))
        out.append(_line(ta + dt.timedelta(seconds=1), host, "WARN", f"{prefix}.rejected", merchant=merchant,
                         cb=cb, reason=reason, attempt=a))
    return out


def _background(rng: random.Random, service: str, day: dt.datetime, n: int):
    out = []
    for _ in range(n):
        t = day + dt.timedelta(seconds=rng.randrange(86400))
        host = rng.choice(HOSTS)
        merchant = rng.choice(MERCHANTS)
        if service.endswith("payouts"):
            out.append(_line(t, host, "INFO", rng.choice(("payout.batch.created", "payout.batch.sent",
                                                          "payout.batch.settled")),
                             merchant=merchant, batch=f"po_{rng.randrange(10**6):06d}",
                             amount=f"{rng.randint(500, 90000)}.{rng.randint(0, 99):02d}"))
            continue
        prefix = "refund.callback" if service.endswith("refunds-hook") else "callback"
        cb = _cb(rng)
        out.append(_line(t, host, "INFO", f"{prefix}.received", merchant=merchant, cb=cb, attempt=1))
        r = rng.random()
        if r < 0.88 or (merchant == TARGET and service == "prod/payments-gw"):
            out.append(_line(t + dt.timedelta(seconds=1), host, "INFO", f"{prefix}.accepted", merchant=merchant,
                             cb=cb, event=rng.choice(EVENTS)))
        else:
            reason = rng.choice(("signature_invalid", "timestamp_skew", "unknown_key", "payload_invalid"))
            if merchant == TARGET:
                reason = "payload_invalid"
            out.append(_line(t + dt.timedelta(seconds=1), host, "WARN", f"{prefix}.rejected", merchant=merchant,
                             cb=cb, reason=reason, attempt=1))
    return out


def _in_window(utc: str) -> bool:
    return WINDOW[0] <= dt.date.fromisoformat(utc[:10]) <= WINDOW[1]


def log_tree() -> dict[str, str]:
    rng = random.Random(4701)
    by_service: dict[str, list[tuple[dt.datetime, str]]] = {}
    plan = [("prod/payments-gw", START, 30), ("prod/refunds-hook", START, 14), ("prod/payouts", START, 12),
            ("staging/payments-gw", dt.datetime(2026, 10, 1, tzinfo=TZ), 12)]
    for service, start, n in plan:
        lines = []
        day = start
        while day < END:
            lines += _background(rng, service, day, n + rng.randint(-4, 4))
            day += dt.timedelta(days=1)
        by_service[service] = lines
    # M-4471 signature rejections outside the window, in September and late October (background for search)
    extra = [(f"2026-09-{d:02d}T{h:02d}:{m:02d}:00", rng.choice((1, 1, 2))) for d, h, m in
             [(3, 10, 5), (8, 15, 40), (11, 7, 7), (19, 21, 15), (24, 12, 30), (29, 18, 0)]]
    extra += [(f"2026-10-{d:02d}T{h:02d}:{m:02d}:00", rng.choice((1, 2))) for d, h, m in
              [(2, 9, 0), (5, 13, 25), (8, 16, 50), (22, 11, 0), (26, 8, 45), (29, 20, 10)]]
    for utc, attempts in TARGET_REJECTIONS + extra:
        by_service["prod/payments-gw"] += _rejection(rng, "prod/payments-gw", TARGET, "signature_invalid", utc,
                                                     attempts)
    for service, merchant, reason, utc, attempts in LOOKALIKES:
        by_service[service] += _rejection(rng, service, merchant, reason, utc, attempts)
    files = {"logs/README.md": LOGS_README}
    for service, lines in by_service.items():
        per_day: dict[str, list[tuple[dt.datetime, str]]] = {}
        for t, line in lines:
            per_day.setdefault(t.astimezone(TZ).date().isoformat(), []).append((t, line))
        for day, entries in sorted(per_day.items()):
            if not START.date().isoformat() <= day < END.date().isoformat():
                continue
            files[f"logs/{service}/{day}.log"] = "".join(line + "\n" for _, line in sorted(entries))
    return files


def sig_failures(*, distinct: bool = True, day_of: str = "utc", merchant_prefix: bool = False,
                 services: tuple[str, ...] = ("logs/prod/payments-gw/",), reasons: tuple[str, ...] = ("signature_invalid",)
                 ) -> list[tuple[str, int]]:
    """The b4-07 answer: distinct M-4471 callbacks the production gateway rejected for an invalid signature, per
    UTC day in the window. The keyword arguments give the plausible wrong answers the trap tests check: counting
    rejection lines, the local (file) date, merchant ids that start with M-4471, other services or reasons."""
    counts: dict[dt.date, set | list] = {WINDOW[0] + dt.timedelta(days=k): (set() if distinct else [])
                                         for k in range((WINDOW[1] - WINDOW[0]).days + 1)}
    for path, text in log_tree().items():
        if not path.startswith(services):
            continue
        for line in text.splitlines():
            parts = line.split()
            kv = dict(p.split("=", 1) for p in parts[4:])
            m = kv["merchant"]
            if (parts[3].endswith("callback.rejected") and kv["reason"] in reasons
                    and (m.startswith(TARGET) if merchant_prefix else m == TARGET)):
                t = dt.datetime.fromisoformat(parts[0])
                day = t.astimezone(dt.UTC).date() if day_of == "utc" else t.date()
                if day in counts:
                    counts[day].add(kv["cb"]) if distinct else counts[day].append(kv["cb"])
    return [(d.isoformat(), len(v)) for d, v in sorted(counts.items())]


def sig_csv(rows: list[tuple[str, int]]) -> str:
    return "date,callbacks\n" + "".join(f"{d},{n}\n" for d, n in rows)


def build(catalog: Path) -> None:
    for seed, files in (("b4a-infra", infra_tree()), ("b4a-logs", log_tree())):
        root = catalog / "seeds" / seed / "files"
        for rel, text in files.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text)


if __name__ == "__main__":
    build(Path(sys.argv[1]))
