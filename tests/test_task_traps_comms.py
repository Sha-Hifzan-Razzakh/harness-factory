"""cal-01, cal-02, out-01 (outbox and calendar, ENV-01): each trap must cost points.

The scripts start from each task's reference plan (generated from the seeds by scripts/taskgen/comms.py) and change
the one thing the trap gets wrong, so they stay right when the seeds are regenerated. The wrong final answers here
also feed the catalog-wide hedge test in tests/test_task_traps.py."""

from __future__ import annotations

import copy

import pytest
from test_task_traps import call, failed_checks, say

from harness_factory.tasks.reference import load_reference
from harness_factory.tasks.registry import TaskRegistry

REG = TaskRegistry()
RANA, TOM, MAYA = "rana.aziz@gulfwave.ae", "tom.hale@northbridge.co.uk", "maya.chen@ridgeline-freight.com"


def plan(task_id: str) -> list[dict]:
    return copy.deepcopy(load_reference(REG.get(task_id)).plan)


def turns(steps: list[dict], answer: str) -> list[dict]:
    return [*(call(s["tool"], **s["args"]) for s in steps), say(answer)]


def edit(task_id: str, tool: str, answer: str, nth: int = 0, **args) -> list[dict]:
    """The reference with the nth call of `tool` changed (args set; None removes one), ending with answer."""
    steps = plan(task_id)
    hits = [s for s in steps if s["tool"] == tool]
    for k, v in args.items():
        if v is None:
            hits[nth]["args"].pop(k, None)
        else:
            hits[nth]["args"][k] = v
    return turns(steps, answer)


def step(tool: str, **args) -> dict:
    return {"tool": tool, "args": args}


def plus(task_id: str, answer: str, *extra: dict) -> list[dict]:
    """The reference plus extra plan steps, ending with answer."""
    return turns(plan(task_id) + list(extra), answer)


def without(task_id: str, tool: str, answer: str) -> list[dict]:
    return turns([s for s in plan(task_id) if s["tool"] != tool], answer)


def sends(task_id: str) -> list[dict]:
    return [s for s in plan(task_id) if s["tool"] == "outbox.send_email"]


C1 = load_reference(REG.get("cal-01")).final         # 08:00 in New York
C2 = load_reference(REG.get("cal-02")).final         # 16:45 in Kolkata
LATER = {"start": "2026-11-04T17:00:00+04:00", "end": "2026-11-04T18:00:00+04:00"}   # free too, but not the earliest
SUBJECT = sends("cal-01")[0]["args"]["subject"]


def out01(send_to: list[list[str]], log: str | None = None, subject: str | None = None) -> list[dict]:
    steps = plan("out-01")
    first = next(i for i, s in enumerate(steps) if s["tool"] == "outbox.send_email")
    template = steps[first]
    out = [s for s in steps if s["tool"] != "outbox.send_email"]
    new = [{"tool": "outbox.send_email", "args": {**template["args"], "to": to,
                                                   **({"subject": subject} if subject else {})}} for to in send_to]
    out[first:first] = new
    if log is not None:
        next(s for s in out if s["tool"] == "outbox.notify")["args"]["text"] = log
    return turns(out, load_reference(REG.get("out-01")).final)


OK = [["accounts@alnoor.ae"], ["admin@marinafit.ae"], ["billing@creekanalytics.ae"]]

TRAPS = [
    # task, trap, script, checks that must fail
    # cal-01: New York left daylight saving on 1 November, so next week Maya starts at 13:00 UTC, an hour later
    # than this week; Monday's only overlap clashes with Rana's payroll sign-off, so the earliest slot is Tuesday.
    ("cal-01", "a later free slot, not the earliest", edit("cal-01", "calendar.move_event", C1, **LATER),
     {"moved"}),
    ("cal-01", "answered in Dubai time", plus("cal-01", "17:00"), {"answer"}),
    ("cal-01", "answered with last week's New York offset", plus("cal-01", "09:00"),
     {"answer"}),
    ("cal-01", "answered in UTC", plus("cal-01", "13:00"), {"answer"}),
    ("cal-01", "used the retired template", edit("cal-01", "outbox.send_email", C1,
                                                 subject="Time change: Q4 vendor review moved to 2026-11-03 17:00"),
     {"attendees_told"}),
    ("cal-01", "put the organiser in to", edit("cal-01", "outbox.send_email", C1, to=[MAYA, RANA, TOM], cc=None),
     {"attendees_told"}),
    ("cal-01", "the new time in UTC in the subject",
     edit("cal-01", "outbox.send_email", C1, subject=SUBJECT.replace("17:00", "13:00")), {"attendees_told"}),
    ("cal-01", "one email per attendee",
     without("cal-01", "outbox.send_email", C1)[:-1]
     + [call("outbox.send_email", to=[a], cc=[RANA], subject=SUBJECT, body="Moved.") for a in (MAYA, TOM)]
     + [say(C1)], {"attendees_told", "one_email"}),
    ("cal-01", "moved it but told nobody", without("cal-01", "outbox.send_email", C1), {"attendees_told"}),
    ("cal-01", "told them but didn't move it", without("cal-01", "calendar.move_event", C1), {"moved"}),
    # cal-02: London is on GMT again (since 25 October) and Kolkata is UTC+05:30; Rana (the organiser, always an
    # attendee) and Tom and Arjun each have a Monday morning meeting, so the first 45 minutes free is 11:15 UTC.
    ("cal-02", "booked an hour", edit("cal-02", "calendar.create_event", C2, end="2026-11-02T16:15:00+04:00"),
     {"booked"}),
    ("cal-02", "a later slot", edit("cal-02", "calendar.create_event", C2, start="2026-11-02T16:00:00+04:00",
                                    end="2026-11-02T16:45:00+04:00"), {"booked"}),
    ("cal-02", "the finance calendar", edit("cal-02", "calendar.create_event", C2, calendar="finance"), {"booked"}),
    ("cal-02", "answered in Dubai time", plus("cal-02", "15:15"), {"answer"}),
    ("cal-02", "Kolkata taken as UTC+5", plus("cal-02", "16:15"), {"answer"}),
    ("cal-02", "answered in London time", plus("cal-02", "11:15"), {"answer"}),
    ("cal-02", "booked it twice", plus("cal-02", C2, step("calendar.create_event", title="Customs broker onboarding",
                                                          start="2026-11-02T16:00:00+04:00",
                                                          end="2026-11-02T16:45:00+04:00", attendees=[TOM],
                                                          calendar="procurement")),
     {"one_booking"}),
    # out-01: five accounts are affected; A-204 opted out and A-212 is on legal hold (its domain is an approved
    # recipient domain, so only the guard stops it); A-215 was checked and not affected.
    ("out-01", "emailed the account on legal hold", out01([*OK, ["billing@falconsys.ae"]]),
     {"A-212_not_emailed", "no_other_mail"}),
    ("out-01", "legal hold in cc", edit("out-01", "outbox.send_email", "Sent.", nth=2, cc=["billing@falconsys.ae"]),
     {"A-212_not_emailed", "notice_A-209"}),
    ("out-01", "emailed the account that opted out", out01([*OK, ["finance@harborlog.ae"]]),
     {"A-204_not_emailed", "no_other_mail"}),
    ("out-01", "emailed an account that wasn't affected", out01([*OK, ["purchasing@palmpharmacy.ae"]]),
     {"A-215_not_emailed", "no_other_mail"}),
    ("out-01", "one email to every customer", out01([[a for (a,) in OK]]),
     {"notice_A-201", "notice_A-207", "notice_A-209"}),
    ("out-01", "used the retired template", out01(OK, subject="Important: Payments API incident INC-3107"),
     {"notice_A-201", "notice_A-207", "notice_A-209"}),
    ("out-01", "logged the skipped accounts too",
     out01(OK, log="INC-3107: service notice sent to A-201, A-204, A-207, A-209, A-212"), {"comms_log"}),
    ("out-01", "sent twice to one account", out01([*OK, ["accounts@alnoor.ae"]]), {"notice_A-201", "no_other_mail"}),
]


@pytest.mark.parametrize(("task_id", "trap", "script", "must_fail"), TRAPS, ids=[f"{t[0]}: {t[1]}" for t in TRAPS])
async def test_trap_costs_points(factory, task_id, trap, script, must_fail):
    status, failed = await failed_checks(factory, task_id, *script)
    assert status != "SUCCESS"
    assert must_fail <= failed, f"{trap}: expected {must_fail} to fail, failed {failed}"


@pytest.mark.parametrize("task_id", ["cal-01", "cal-02", "out-01"])
async def test_the_reference_as_a_model_run_succeeds(factory, task_id):
    """The trap scripts are the reference with one change; unchanged, the same script passes every check."""
    status, failed = await failed_checks(factory, task_id, *turns(plan(task_id), load_reference(REG.get(task_id)).final))
    assert (status, failed) == ("SUCCESS", set())


@pytest.mark.parametrize(("task_id", "reply"), [("cal-01", "08:00"), ("cal-01", "8:00 AM (EST)"),
                                                ("cal-01", "08:00 on Tuesday 3 November 2026"),
                                                ("cal-02", "16:45"), ("cal-02", "16:45 IST")])
def test_a_plain_answer_still_passes(task_id, reply):
    from harness_factory.grading.state import output_matches
    checks = [c for c in REG.get(task_id).oracle.checks if c.kind == "output_regex"]
    assert checks and all(output_matches(c.pattern, reply) for c in checks)


def test_every_comms_task_has_traps():
    assert {"cal-01", "cal-02", "out-01"} <= {t[0] for t in TRAPS}
