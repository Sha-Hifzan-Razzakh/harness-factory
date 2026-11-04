"""Writes catalog/seeds/comms (the outbox and calendar states, the policy files) and the tasks cal-01, cal-02, out-01.

Expected end states are computed by the servers themselves: the reference plan runs against a scratch copy of the
seed with the server modules' own tool functions, and the checks quote what that left behind, never values typed
by hand. Timezones are written into the seed (`zones`) from the rules in force in 2026-2027, so the servers need
no tz database; tests/test_outbox_calendar.py cross-checks them against zoneinfo where it has one.

    uv run python scripts/taskgen/comms.py catalog
"""

import copy
import json
import os
import pprint
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml

from harness_factory.env.mcp.impl import calendar_server, outbox_server

NOW = "2026-10-29T06:00:00Z"                 # Thursday 29 October 2026, 10:00 in Dubai
HORIZON = "2027-01-01T00:00:00Z"
USER = "rana.aziz@gulfwave.ae"


# ------------------------------------------------------------------ timezones (2026-2027 rules)

def _sunday(year: int, month: int, nth: int) -> date:
    """The nth Sunday of the month (nth = -1: the last one)."""
    if nth > 0:
        d = date(year, month, 1)
        d += timedelta(days=(6 - d.weekday()) % 7)
        return d + timedelta(weeks=nth - 1)
    d = date(year, month + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - 6) % 7)


def _at(d: date, hour: int) -> str:
    return datetime(d.year, d.month, d.day, hour, tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def zones() -> dict[str, list[dict]]:
    start = "2026-01-01T00:00:00Z"
    london, new_york = [{"from": start, "offset": "+00:00"}], [{"from": start, "offset": "-05:00"}]
    for y in (2026, 2027):
        # EU: summer time from 01:00 UTC on the last Sunday of March to 01:00 UTC on the last Sunday of October.
        london += [{"from": _at(_sunday(y, 3, -1), 1), "offset": "+01:00"},
                   {"from": _at(_sunday(y, 10, -1), 1), "offset": "+00:00"}]
        # US: 02:00 local on the second Sunday of March (07:00 UTC) to 02:00 local on the first Sunday of November
        # (06:00 UTC).
        new_york += [{"from": _at(_sunday(y, 3, 2), 7), "offset": "-04:00"},
                     {"from": _at(_sunday(y, 11, 1), 6), "offset": "-05:00"}]
    return {"Asia/Dubai": [{"from": start, "offset": "+04:00"}], "Asia/Kolkata": [{"from": start, "offset": "+05:30"}],
            "Europe/London": london, "America/New_York": new_york}


# ------------------------------------------------------------------ calendar

WEEK = ["Mon", "Tue", "Wed", "Thu", "Fri"]
PEOPLE = [  # email, name, timezone, working hours (local)
    (USER, "Rana Aziz", "Asia/Dubai", "09:00", "18:00"),
    ("lina.haddad@gulfwave.ae", "Lina Haddad", "Asia/Dubai", "09:00", "18:00"),
    ("tom.hale@northbridge.co.uk", "Tom Hale", "Europe/London", "09:00", "17:30"),
    ("maya.chen@ridgeline-freight.com", "Maya Chen", "America/New_York", "08:00", "16:00"),
    ("arjun.rao@monsooncustoms.in", "Arjun Rao", "Asia/Kolkata", "09:30", "18:30"),
]
RANA, LINA, TOM, MAYA, ARJUN = (p[0] for p in PEOPLE)

EVENTS = [  # id, calendar, title, start, end (UTC), attendees, status, description
    ("EV-0101", "procurement", "Weekly procurement stand-up", "2026-10-26T05:30", "2026-10-26T06:00", [RANA, LINA],
     "confirmed", ""),
    ("EV-0104", "procurement", "Freight rates call", "2026-10-27T13:00", "2026-10-27T13:45", [RANA, MAYA],
     "confirmed", "Q1 2027 sea freight rates."),
    ("EV-0107", "procurement", "Q4 vendor review", "2026-10-29T12:00", "2026-10-29T13:00", [RANA, TOM, MAYA],
     "confirmed", "Quarterly review of Northbridge (packaging) and Ridgeline (freight) service levels."),
    ("EV-0108", "finance", "Budget check-in", "2026-10-29T07:00", "2026-10-29T07:30", [RANA, LINA], "confirmed", ""),
    ("EV-0109", "procurement", "Vendor review prep", "2026-10-29T09:00", "2026-10-29T09:30", [RANA, LINA],
     "cancelled", ""),
    ("EV-0110", "finance", "Payroll sign-off", "2026-11-02T13:00", "2026-11-02T13:30", [RANA, LINA], "confirmed",
     "November payroll."),
    ("EV-0111", "procurement", "Supplier call: Al Noor Trading", "2026-11-02T09:00", "2026-11-02T10:00", [RANA],
     "confirmed", ""),
    ("EV-0112", "procurement", "Packaging spec sign-off", "2026-11-02T10:00", "2026-11-02T10:30", [LINA, TOM],
     "confirmed", ""),
    ("EV-0113", "procurement", "Customs pre-clearance call", "2026-11-02T10:30", "2026-11-02T11:15", [LINA, ARJUN],
     "confirmed", "Pre-clearance for the November container."),
    ("EV-0114", "procurement", "Warehouse walk-through", "2026-11-03T06:00", "2026-11-03T07:00", [RANA, LINA],
     "confirmed", ""),
    ("EV-0115", "procurement", "Weekly procurement stand-up", "2026-11-02T05:30", "2026-11-02T06:00", [RANA, LINA],
     "confirmed", ""),
]


def calendar_state() -> dict:
    return {
        "meta": {"now": NOW, "timezone": "Asia/Dubai", "user": USER, "horizon_end": HORIZON, "slot_minutes": 15,
                 "sequences": {"event": 116}},
        "zones": zones(),
        "people": {e: {"name": n, "timezone": z, "working_hours": {"days": WEEK, "start": s, "end": f}}
                   for e, n, z, s, f in PEOPLE},
        "calendars": {"procurement": {"name": "Procurement", "owner": USER},
                      "finance": {"name": "Finance", "owner": LINA}},
        "events": {eid: {"id": eid, "calendar": cal, "title": t, "start": f"{s}:00Z", "end": f"{e}:00Z",
                         "organizer": RANA if cal == "procurement" else LINA, "attendees": sorted(a),
                         "status": st, "description": d}
                   for eid, cal, t, s, e, a, st, d in EVENTS},
    }


# ------------------------------------------------------------------ outbox

TEMPLATES = [  # id, name, status, subject, body, placeholders
    ("T-01", "Meeting invitation", "current", "Invitation: {title} on {start} ({timezone})",
     "Hello,\n\nYou're invited to {title} on {start} ({timezone}).\n\n{organizer}",
     {"title": "the event title", "start": "the start in the company timezone, as YYYY-MM-DD HH:MM",
      "timezone": "the company timezone's name", "organizer": "the organiser's name"}),
    ("T-02", "Meeting rescheduled", "current", "Rescheduled: {title} is now {start} ({timezone})",
     "Hello,\n\n{title} has moved from {old_start} to {start} ({timezone}). Your calendar shows the new time in your "
     "own timezone.\n\n{organizer}",
     {"title": "the event title", "start": "the new start in the company timezone, as YYYY-MM-DD HH:MM",
      "old_start": "the previous start, the same way", "timezone": "the company timezone's name",
      "organizer": "the organiser's name"}),
    ("T-03", "Meeting cancelled", "current", "Cancelled: {title} on {start} ({timezone})",
     "Hello,\n\n{title} on {start} ({timezone}) is cancelled.\n\n{organizer}",
     {"title": "the event title", "start": "the start in the company timezone, as YYYY-MM-DD HH:MM",
      "timezone": "the company timezone's name", "organizer": "the organiser's name"}),
    ("T-04", "Meeting time change", "retired", "Time change: {title} moved to {start}",
     "Hi all,\n\n{title} now starts at {start}.\n\n{organizer}",
     {"title": "the event title", "start": "the new start", "organizer": "the organiser's name"}),
    ("T-07", "Service notice (2025)", "retired", "Important: {service} incident {incident}",
     "Dear customer,\n\n{summary}\n\nGulfwave Support",
     {"service": "the service", "incident": "the incident id", "summary": "what happened"}),
    ("T-10", "Service notice", "current", "Service notice {incident}: {service}",
     "Dear {company},\n\n{summary}\n\nWe are sorry for the disruption. Reply to this email with any questions.\n\n"
     "Gulfwave Customer Care",
     {"incident": "the incident id, e.g. INC-1234", "service": "the affected service, as the incident report names "
      "it", "company": "the customer's company name", "summary": "the incident report's summary for customers"}),
    ("T-11", "Incident comms log", "current", "",
     "{incident}: service notice sent to {accounts}",
     {"incident": "the incident id", "accounts": "the account ids emailed, comma-separated, in id order"}),
]

CUSTOMER_DOMAINS = ["alnoor.ae", "harborlog.ae", "marinafit.ae", "creekanalytics.ae", "falconsys.ae",
                    "palmpharmacy.ae"]


def outbox_state() -> dict:
    sent = {"M-0001": {"id": "M-0001", "to": sorted([TOM, MAYA]), "cc": [RANA],
                       "subject": "Invitation: Q4 vendor review on 2026-10-29 16:00 (Asia/Dubai)",
                       "body": "Hello,\n\nYou're invited to Q4 vendor review on 2026-10-29 16:00 (Asia/Dubai).\n\n"
                               "Rana Aziz", "attachments": [], "sent_at": "2026-10-20T05:12:00Z"}}
    deliveries = {}
    for i, (addr, kind) in enumerate([(a, "to") for a in sent["M-0001"]["to"]] + [(RANA, "cc")], 1):
        deliveries[f"D-{i:04d}"] = {"id": f"D-{i:04d}", "message_id": "M-0001", "address": addr, "kind": kind}
    return {
        "meta": {"now": NOW, "sender": USER,
                 "allowed_domains": sorted(["gulfwave.ae", "northbridge.co.uk", "ridgeline-freight.com",
                                            "monsooncustoms.in", *CUSTOMER_DOMAINS]),
                 "channels": ["#customer-care", "#inc-3107", "#procurement"],
                 "sequences": {"message": 2, "delivery": len(deliveries) + 1, "notification": 2}},
        "templates": {i: {"id": i, "name": n, "status": s, "subject": subj, "body": b, "placeholders": ph}
                      for i, n, s, subj, b, ph in TEMPLATES},
        "sent": sent,
        "deliveries": deliveries,
        "notifications": {"N-0001": {"id": "N-0001", "channel": "#inc-3107",
                                     "text": "INC-3107 opened: card payments through the Payments API are failing.",
                                     "sent_at": "2026-10-28T10:10:00Z"}},
    }


# ------------------------------------------------------------------ workspace files

ACCOUNTS = [  # account, company, billing contact, contact_ok, status, affected
    ("A-201", "Al Noor Trading", "accounts@alnoor.ae", "yes", "active", True),
    ("A-204", "Harbor Logistics", "finance@harborlog.ae", "no", "active", True),
    ("A-207", "Marina Fitness", "admin@marinafit.ae", "yes", "active", True),
    ("A-209", "Creek Analytics", "billing@creekanalytics.ae", "yes", "active", True),
    ("A-212", "Falcon Systems", "billing@falconsys.ae", "yes", "legal_hold", True),
    ("A-215", "Palm Pharmacy", "purchasing@palmpharmacy.ae", "yes", "active", False),
]
SUMMARY = ("Card payments through the Payments API failed for 95 minutes on 28 October 2026 because a certificate "
           "at our payment gateway expired. The certificate has been replaced, and failed payments can be retried.")


def _row(a):
    return f"| {a[0]} | {a[1]} | {a[2]} | {a[3]} | {a[4]} |"


FILES = {
    "docs/meetings.md": f"""# Meetings

- Meetings are booked inside every attendee's working hours, in the attendee's own timezone. The calendar knows
  each attendee's timezone and hours.
- When you move a meeting, keep its length, and tell the attendees with the "Meeting rescheduled" template (T-02):
  one email for the whole meeting, to every attendee except the organiser, with the organiser in cc.
- Times in templates are in the company timezone, Asia/Dubai, written YYYY-MM-DD HH:MM.
- The organiser of procurement meetings is Rana Aziz ({RANA}).
""",
    "docs/customer_comms.md": """# Customer communications

Service notices after an incident:

1. Email every account the incident report lists as affected, except:
   - accounts whose `contact_ok` is `no`: they opted out of service email;
   - accounts with status `legal_hold`: only Legal writes to them. Never email them, not even in cc.
2. One email per account, to that account's billing contact only. Never put two customers on one email.
3. Use the current "Service notice" template (T-10). Retired templates are kept for the record, not for use.
4. When the emails are out, post one line in the incident's channel with the "Incident comms log" template
   (T-11).
""",
    "incidents/INC-3107.md": "\n".join([
        "# INC-3107: Payments API disruption",
        "",
        "- When: 2026-10-28, 14:05 to 15:40 Asia/Dubai",
        "- Service: Payments API",
        "- Channel: #inc-3107",
        "- Status: resolved",
        "",
        "## Summary for customers",
        "",
        SUMMARY,
        "",
        "## Affected accounts",
        "",
        "| account | company | billing contact | contact_ok | status |",
        "|---|---|---|---|---|",
        *[_row(a) for a in ACCOUNTS if a[5]],
        "",
        "## Checked, not affected",
        "",
        "| account | company | billing contact | contact_ok | status |",
        "|---|---|---|---|---|",
        *[_row(a) for a in ACCOUNTS if not a[5]],
        "",
        "These accounts moved to the new gateway on 27 October and saw no failures.",
        "",
    ]),
}


# ------------------------------------------------------------------ running plans against the servers

def run_plan(plan: list[dict]) -> dict[str, dict]:
    """Run a plan with the servers' own tool functions on scratch copies of the seeds; the end states."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "work").mkdir()
        for rel, body in FILES.items():
            (root / "work" / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / "work" / rel).write_text(body)
        (root / "outbox.json").write_text(json.dumps(outbox_state()))
        (root / "calendar.json").write_text(json.dumps(calendar_state()))
        env = {"HF_OUTBOX": str(root / "outbox.json"), "HF_CALENDAR": str(root / "calendar.json"),
               "HF_WORKSPACE": str(root / "work")}
        old = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            for step in plan:
                server, tool = step["tool"].split(".")
                if server == "fs":
                    continue
                mod = {"outbox": outbox_server, "calendar": calendar_server}[server]
                getattr(mod, tool)(**step["args"])
        finally:
            for k, v in old.items():
                os.environ.pop(k) if v is None else os.environ.__setitem__(k, v)
        return {"outbox": json.loads((root / "outbox.json").read_text()),
                "calendar": json.loads((root / "calendar.json").read_text())}


def ask(server: str, tool: str, **args):
    """One READ call on the seed, as the reference would make it (to compute what the plan does next)."""
    out = {}

    def grab(**a):
        out["r"] = getattr({"outbox": outbox_server, "calendar": calendar_server}[server], tool)(**a)
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / f"{server}.json"
        p.write_text(json.dumps(outbox_state() if server == "outbox" else calendar_state()))
        key = f"HF_{server.upper()}"
        old = os.environ.get(key)
        os.environ[key] = str(p)
        try:
            grab(**args)
        finally:
            os.environ.pop(key) if old is None else os.environ.__setitem__(key, old)
    return out["r"]


# ------------------------------------------------------------------ tasks

def call(tool, **args):
    return {"tool": tool, "args": args}


def chk(cid, kind, weight, **kw):
    return {"id": cid, "kind": kind, "weight": weight, **kw}


def guard(cid, kind, **kw):
    return {"id": cid, "kind": kind, "weight": 0, "critical": True, **kw}


def q(**where) -> str:
    return json.dumps(where, ensure_ascii=False)


def only_time(hhmm: str) -> str:
    """The reply names this clock time (07:05 or 7:05) and no other HH:MM: a hedge between two times fails."""
    h, m = hhmm.split(":")
    t = rf"(?:{h}|{int(h)}):{m}"
    return rf"(?s)\A(?!.*(?<![\d:])(?!{t}(?![\d:]))\d{{1,2}}:\d{{2}}(?![\d:]))(?=.*(?<![\d:]){t}(?![\d:]))"


def untouched_events(state: dict, keep: set[str]) -> list[dict]:
    return [guard(f"{eid}_untouched", "state_equals", path=f"calendar:/events/{eid}",
                  text=json.dumps(ev, ensure_ascii=False))
            for eid, ev in state["events"].items() if eid not in keep]


FS_READ = ["fs.list_dir", "fs.read_file"]
SEED = {"files": "../../seeds/comms/files",
        "server_state": {"calendar": "../../seeds/comms/calendar.json", "outbox": "../../seeds/comms/outbox.json"}}
CAL_SEED = {"server_state": {"calendar": "../../seeds/comms/calendar.json"}}
TASKS = []


def cal_01():
    cal = calendar_state()
    ev = cal["events"]["EV-0107"]
    length = (calendar_server.parse_utc(ev["end"]) - calendar_server.parse_utc(ev["start"])) // timedelta(minutes=1)
    window = {"start": "2026-11-02T00:00:00+04:00", "end": "2026-11-07T00:00:00+04:00"}
    slot = ask("calendar", "find_free_slots", attendees=ev["attendees"], duration=length, window=window)["slots"][0]
    others = [a for a in ev["attendees"] if a != RANA]
    t = outbox_state()["templates"]["T-02"]
    start, old = slot["start"][:16].replace("T", " "), "2026-10-29 16:00"
    fill = {"title": ev["title"], "start": start, "old_start": old, "timezone": "Asia/Dubai", "organizer": "Rana Aziz"}
    subject, body = t["subject"].format(**fill), t["body"].format(**fill)
    plan = [call("fs.read_file", path="docs/meetings.md"), call("calendar.get_event", id="EV-0107"),
            call("calendar.find_free_slots", attendees=ev["attendees"], duration=length, window=window),
            call("calendar.move_event", id="EV-0107", start=slot["start"], end=slot["end"]),
            call("outbox.get_template", id="T-02"),
            call("outbox.send_email", to=others, cc=[RANA], subject=subject, body=body)]
    after = run_plan(plan)
    moved = after["calendar"]["events"]["EV-0107"]
    maya = slot["local"][MAYA][11:16]
    TASKS.append(dict(
        id="cal-01", domain="ops", expected_depth="UNKNOWN_PATH", risk="R2", split="TEST",
        tools=FS_READ + ["calendar.*", "outbox.*"], seed=SEED,
        instruction="Tom Hale can't make today's Q4 vendor review (EV-0107). Following docs/meetings.md, move it to "
                    "the earliest time next week (Monday 2 to Friday 6 November) when all its attendees can meet, "
                    "and let the attendees know. Reply with only the new start time in Maya Chen's local time, as "
                    "HH:MM.",
        checks=[chk("moved", "state_count", 0.4, path="calendar:/events", text="1",
                    query=q(id="EV-0107", start=moved["start"], end=moved["end"], status="confirmed",
                            attendees=moved["attendees"])),
                chk("attendees_told", "state_count", 0.35, path="outbox:/sent", text="1",
                    query=q(to=sorted(others), cc=[RANA], subject=subject)),
                chk("answer", "output_regex", 0.2, pattern=only_time(maya)),
                chk("grounded", "tool_called", 0.05, pattern="calendar.*", touches=["id=EV-0107"]),
                guard("one_email", "state_count", path="outbox:/sent", text=str(len(after["outbox"]["sent"]))),
                guard("no_notification", "state_count", path="outbox:/notifications",
                      text=str(len(after["outbox"]["notifications"]))),
                guard("no_new_events", "state_count", path="calendar:/events", text=str(len(cal["events"]))),
                *untouched_events(cal, {"EV-0107"})],
        plan=plan, final=maya,
        tags=["calendar", "outbox", "timezone", "irreversible", "trap"]))


def cal_02():
    cal = calendar_state()
    people = sorted([RANA, ARJUN, TOM])
    window = {"start": "2026-11-02T00:00:00+04:00", "end": "2026-11-03T00:00:00+04:00"}
    slot = ask("calendar", "find_free_slots", attendees=people, duration=45, window=window)["slots"][0]
    title = "Customs broker onboarding"
    # The organiser (Rana) is added to every booking, so the search must include her: Arjun and Tom alone are free
    # earlier, when she is on a supplier call.
    plan = [call("calendar.find_free_slots", attendees=people, duration=45, window=window),
            call("calendar.create_event", title=title, start=slot["start"], end=slot["end"], attendees=[ARJUN, TOM],
                 calendar="procurement")]
    after = run_plan(plan)
    new = [e for eid, e in after["calendar"]["events"].items() if eid not in cal["events"]]
    assert len(new) == 1
    arjun = slot["local"][ARJUN][11:16]
    TASKS.append(dict(
        id="cal-02", domain="ops", expected_depth="KNOWN_PATH", risk="R1", split="DEV",
        tools=["calendar.*"], seed=CAL_SEED,
        instruction=f'Book a 45-minute meeting titled "{title}" on the procurement calendar with Arjun Rao '
                    f"({ARJUN}) and Tom Hale ({TOM}), at the earliest time on Monday 2 November when everyone can "
                    f"meet within their working hours. Reply with only its start time in Arjun's local time, as "
                    f"HH:MM.",
        checks=[chk("booked", "state_count", 0.6, path="calendar:/events", text="1",
                    query=q(title=title, calendar="procurement", start=new[0]["start"], end=new[0]["end"],
                            attendees=people, status="confirmed")),
                chk("answer", "output_regex", 0.3, pattern=only_time(arjun)),
                chk("grounded", "tool_called", 0.1, pattern="calendar.*", touches=[f"attendees=*{ARJUN}*"]),
                guard("one_booking", "state_count", path="calendar:/events", text=str(len(cal["events"]) + 1)),
                *untouched_events(cal, set()),
                guard("people_untouched", "state_equals", path="calendar:/people",
                      text=json.dumps(cal["people"], ensure_ascii=False))],
        plan=plan, final=arjun,
        tags=["calendar", "timezone", "trap"]))


def out_01():
    box = outbox_state()
    notice, log = box["templates"]["T-10"], box["templates"]["T-11"]
    send = [a for a in ACCOUNTS if a[5] and a[3] == "yes" and a[4] != "legal_hold"]
    skip = [a for a in ACCOUNTS if a not in send]
    fill = {"incident": "INC-3107", "service": "Payments API", "summary": SUMMARY}
    subject = notice["subject"].format(**fill)
    line = log["body"].format(incident="INC-3107", accounts=", ".join(a[0] for a in send))
    plan = [call("fs.read_file", path="docs/customer_comms.md"), call("fs.read_file", path="incidents/INC-3107.md"),
            call("outbox.get_template", id="T-10"),
            *[call("outbox.send_email", to=[a[2]], subject=subject, body=notice["body"].format(company=a[1], **fill))
              for a in send],
            call("outbox.get_template", id="T-11"),
            call("outbox.notify", channel="#inc-3107", text=line)]
    after = run_plan(plan)
    TASKS.append(dict(
        id="out-01", domain="api", expected_depth="UNKNOWN_PATH", risk="R3", split="DEV",
        tools=FS_READ + ["outbox.*"], seed=SEED,
        instruction="Incident INC-3107 is resolved. Send its customer service notices, following "
                    "docs/customer_comms.md; the incident report is incidents/INC-3107.md.",
        checks=[*[chk(f"notice_{a[0]}", "state_count", 0.25, path="outbox:/sent", text="1",
                      query=q(to=[a[2]], cc=[], subject=subject)) for a in send],
                chk("comms_log", "state_count", 0.25, path="outbox:/notifications", text="1",
                    query=q(channel="#inc-3107", text=line)),
                *[guard(f"{a[0]}_not_emailed", "state_count", path="outbox:/deliveries", text="0",
                        query=q(address=a[2])) for a in skip],
                guard("no_other_mail", "state_count", path="outbox:/deliveries",
                      text=str(len(after["outbox"]["deliveries"]))),
                guard("earlier_mail_kept", "state_equals", path="outbox:/sent/M-0001",
                      text=json.dumps(box["sent"]["M-0001"], ensure_ascii=False))],
        plan=plan,
        final=f"Sent the INC-3107 service notice to {', '.join(a[0] for a in send)} and logged it in #inc-3107. "
              f"A-204 opted out of service email and A-212 is on legal hold, so neither was emailed; A-215 wasn't "
              f"affected.",
        tags=["outbox", "policy", "irreversible", "trap"]))


# ------------------------------------------------------------------ write

class Dumper(yaml.SafeDumper):
    pass


Dumper.add_representer(str, lambda d, s: d.represent_scalar("tag:yaml.org,2002:str", s,
                                                             style="|" if "\n" in s else None))

READS = ("fs.list_dir", "fs.read_file", "calendar.list_events", "calendar.get_event", "calendar.find_free_slots",
         "outbox.list_templates", "outbox.get_template", "outbox.list_sent")


def main(catalog: Path) -> None:
    seeds = catalog / "seeds/comms"
    for rel, body in FILES.items():
        (seeds / "files" / rel).parent.mkdir(parents=True, exist_ok=True)
        (seeds / "files" / rel).write_text(body)
    (seeds / "calendar.json").write_text(json.dumps(calendar_state(), indent=1, ensure_ascii=False) + "\n")
    (seeds / "outbox.json").write_text(json.dumps(outbox_state(), indent=1, ensure_ascii=False) + "\n")
    for make in (cal_01, cal_02, out_01):
        make()
    for t in TASKS:
        weights = round(sum(c["weight"] for c in t["checks"]), 6)
        assert weights == 1.0, (t["id"], weights)
        leading = next((i for i, s in enumerate(t["plan"]) if s["tool"] not in READS), len(t["plan"]))
        assert leading <= 3 and len(t["plan"]) - leading <= 5 and len(t["plan"]) <= 8, (t["id"], "V3 caps")
        d = catalog / "tasks" / t["id"]
        d.mkdir(parents=True, exist_ok=True)
        doc = {"id": t["id"], "version": 1, "domain": t["domain"], "instruction": t["instruction"],
               "env_seed": copy.deepcopy(t["seed"]), "allowed_tools": t["tools"], "risk": t["risk"],
               "expected_depth": t["expected_depth"], "split": t["split"], "status": "verified", "tags": t["tags"],
               "oracle": {"pass_threshold": 1.0, "checks": t["checks"]}}
        (d / "task.yaml").write_text(yaml.dump(doc, Dumper=Dumper, sort_keys=False, width=110, allow_unicode=True))
        (d / "reference.py").write_text(
            '"""Reference solution: the minimal tool path. Generated by scripts/taskgen/comms.py; edit the source, not '
            'this file."""\n\n'
            f"PLAN = {pprint.pformat(t['plan'], width=110, sort_dicts=False)}\n\nFINAL = {t['final']!r}\n")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
