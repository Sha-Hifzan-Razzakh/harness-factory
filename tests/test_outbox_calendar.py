"""The outbox and calendar servers (ENV-01): their tools and refusals, timezone math, the per-run JSON states, the
state_equals / state_count checks, the policy on IRREVERSIBLE sends, and that adding them moved no existing
identity."""

import json
from datetime import UTC, datetime

import pytest

from harness_factory import settings
from harness_factory.env.mcp.impl import calendar_server as cal
from harness_factory.env.sandbox import LocalSandbox
from harness_factory.env.state import EnvState, StateInspector
from harness_factory.grading.base import GradeContext
from harness_factory.grading.state import StateDiffGrader
from harness_factory.hashing import hash_paths
from harness_factory.mcp.pool import MCPSessionPool
from harness_factory.provision.provisioner import reaches, task_class
from harness_factory.runner.runner import plan, run_one
from harness_factory.shapes.result import RunResult
from harness_factory.tasks.model import EnvSeed, Oracle, Task, WeightedCheck

RANA, TOM, MAYA = "rana.aziz@gulfwave.ae", "tom.hale@northbridge.co.uk", "maya.chen@ridgeline-freight.com"
ARJUN = "arjun.rao@monsooncustoms.in"
SEEDS = settings.CATALOG / "seeds/comms"


@pytest.fixture
def comms(factory):
    with LocalSandbox("comms-test") as sb:
        sb.reset(factory.tasks.get("cal-01"))              # seeds files, outbox and calendar
        yield sb


async def _call(pool, ref, **args):
    res = await pool.call(ref, args)
    return res if res.is_error else json.loads(res.text)


def _state(sb, name) -> dict:
    return json.loads(sb.state_path(name).read_text())


async def _refused(pool, ref, msg, **args):
    res = await pool.call(ref, args)
    assert res.is_error and msg in res.text, (ref, args, res.text)     # ToolError: the model sees why


# ------------------------------------------------------------------ outbox

async def test_send_email_records_the_message_and_one_delivery_per_recipient(factory, comms):
    (comms.workspace / "notes.txt").write_text("agenda")
    async with MCPSessionPool(factory.declared, ["outbox"], comms.env()) as pool:
        msg = await _call(pool, "outbox.send_email", to=[" Tom.Hale@Northbridge.co.uk", MAYA, MAYA],
                          cc=[RANA, TOM], subject=" Agenda ", body="See attached.", attachments=["./notes.txt"])
    assert msg["id"] == "M-0002" and msg["sent_at"] == "2026-10-29T06:00:00Z"        # the seed's sequence and now
    assert (msg["to"], msg["cc"], msg["subject"]) == ([MAYA, TOM], [RANA], "Agenda")  # lower-cased, sorted, deduped
    assert msg["attachments"] == [{"path": "notes.txt", "bytes": 6, "sha256": msg["attachments"][0]["sha256"]}]
    state = _state(comms, "outbox")
    assert state["sent"]["M-0002"] == msg
    new = [d for d in state["deliveries"].values() if d["message_id"] == "M-0002"]
    assert sorted((d["address"], d["kind"]) for d in new) == [(MAYA, "to"), (RANA, "cc"), (TOM, "to")]
    assert state["meta"]["sequences"] == {"message": 3, "delivery": 7, "notification": 2}


async def test_send_email_refusals(factory, comms):
    (comms.root / "secret.txt").write_text("outside")
    ok = {"to": [TOM], "subject": "s", "body": "b"}
    async with MCPSessionPool(factory.declared, ["outbox"], comms.env()) as pool:
        for args, msg in (({**ok, "to": ["someone@gmail.com"]}, "gmail.com is not an approved recipient domain"),
                          ({**ok, "cc": ["x@unknown.example"]}, "not an approved recipient domain"),
                          ({**ok, "to": ["not-an-address"]}, "is not an email address"),
                          ({**ok, "to": ["a@gulfwave.ae, b@gulfwave.ae"]}, "is not an email address"),
                          ({**ok, "to": []}, "to is empty"),
                          ({**ok, "subject": "  "}, "subject is missing"),
                          ({**ok, "body": ""}, "body is missing"),
                          ({**ok, "subject": "x" * 201}, "subject is too long"),
                          ({**ok, "attachments": ["missing.pdf"]}, "is not a file in the workspace"),
                          ({**ok, "attachments": ["../secret.txt"]}, "is outside the workspace"),
                          ({**ok, "attachments": ["docs"]}, "is not a file in the workspace")):
            await _refused(pool, "outbox.send_email", msg, **args)
    assert len(_state(comms, "outbox")["sent"]) == 1                                  # nothing was sent


async def test_notify_templates_and_list_sent(factory, comms):
    async with MCPSessionPool(factory.declared, ["outbox"], comms.env()) as pool:
        await _refused(pool, "outbox.notify", "unknown channel '#general'", channel="#general", text="hi")
        await _refused(pool, "outbox.notify", "text is missing", channel="#procurement", text=" ")
        note = await _call(pool, "outbox.notify", channel="#procurement", text="Vendor review moved.")
        assert note == {"id": "N-0002", "channel": "#procurement", "text": "Vendor review moved.",
                        "sent_at": "2026-10-29T06:00:00Z"}
        templates = (await _call(pool, "outbox.list_templates"))["templates"]
        assert {t["id"]: t["status"] for t in templates}["T-04"] == "retired"
        t02 = await _call(pool, "outbox.get_template", id="T-02")
        assert "{start}" in t02["subject"] and "start" in t02["placeholders"]
        await _refused(pool, "outbox.get_template", "unknown template id 'T-99'", id="T-99")
        sent = await _call(pool, "outbox.list_sent")
        assert [m["id"] for m in sent["emails"]] == ["M-0001"] and "body" not in sent["emails"][0]
        assert [n["id"] for n in sent["notifications"]] == ["N-0001", "N-0002"]


async def test_a_task_without_the_states_gets_a_clear_error(factory):
    with LocalSandbox("comms-none") as sb:
        sb.reset(factory.tasks.get("fs-01"))
        assert not sb.state_path("outbox").exists() and not sb.state_path("calendar").exists()
        async with MCPSessionPool(factory.declared, ["outbox", "calendar"], sb.env()) as pool:
            res = await pool.call("outbox.list_sent", {})
            assert res.is_error and "no outbox state was seeded" in res.text
            res = await pool.call("calendar.get_event", {"id": "EV-0107"})
            assert res.is_error and "no calendar state was seeded" in res.text


# ------------------------------------------------------------------ calendar

async def test_events_are_shown_in_the_business_timezone_with_each_attendees_local_time(factory, comms):
    async with MCPSessionPool(factory.declared, ["calendar"], comms.env()) as pool:
        ev = await _call(pool, "calendar.get_event", id="EV-0107")
        assert (ev["start"], ev["start_utc"], ev["timezone"]) == ("2026-10-29T16:00:00+04:00",
                                                                  "2026-10-29T12:00:00Z", "Asia/Dubai")
        local = {a["email"]: a["local_start"] for a in ev["attendees"]}
        assert local == {MAYA: "2026-10-29T08:00:00-04:00",          # New York still on daylight time
                         RANA: "2026-10-29T16:00:00+04:00",
                         TOM: "2026-10-29T12:00:00+00:00"}           # London back on GMT since 25 October
        week = await _call(pool, "calendar.list_events", start="2026-11-02T00:00:00+04:00",
                           end="2026-11-03T00:00:00+04:00")
        assert [e["id"] for e in week["events"]] == ["EV-0115", "EV-0111", "EV-0112", "EV-0113", "EV-0110"]
        assert week["now"] == "2026-10-29T10:00:00+04:00"
        fin = await _call(pool, "calendar.list_events", start="2026-10-29T00:00:00Z", end="2026-10-30T00:00:00Z",
                          calendar="finance")
        assert [e["id"] for e in fin["events"]] == ["EV-0108"]          # EV-0109 is cancelled: not listed
        for args, msg in (({"start": "2026-11-02T00:00", "end": "2026-11-03T00:00:00Z"}, "has no UTC offset"),
                          ({"start": "Monday", "end": "2026-11-03T00:00:00Z"}, "is not an ISO 8601 time"),
                          ({"start": "2026-11-03T00:00:00Z", "end": "2026-11-02T00:00:00Z"}, "end must be after"),
                          ({"start": "2026-11-01T00:00:00Z", "end": "2026-12-15T00:00:00Z"}, "at most 31 days"),
                          ({"start": "2026-11-01T00:00:00Z", "end": "2026-11-02T00:00:00Z", "calendar": "hr"},
                           "unknown calendar 'hr'")):
            await _refused(pool, "calendar.list_events", msg, **args)
        await _refused(pool, "calendar.get_event", "unknown event id 'EV-9'", id="EV-9")


async def test_bookings_respect_working_hours_conflicts_and_now(factory, comms):
    base = {"title": "Sync", "attendees": [MAYA, TOM], "calendar": "procurement"}
    async with MCPSessionPool(factory.declared, ["calendar"], comms.env()) as pool:
        for args, msg in (
                # 12:00 UTC on 2 November is 07:00 in New York: daylight time ended on 1 November
                ({"start": "2026-11-02T16:00:00+04:00", "end": "2026-11-02T17:00:00+04:00"},
                 "outside working hours: maya.chen@ridgeline-freight.com works Mon/Tue/Wed/Thu/Fri 08:00-16:00"),
                ({"start": "2026-11-02T13:00:00Z", "end": "2026-11-02T14:00:00Z"},
                 "conflict: rana.aziz@gulfwave.ae is busy from 2026-11-02T17:00:00+04:00"),   # payroll sign-off
                ({"start": "2026-10-31T13:00:00Z", "end": "2026-10-31T14:00:00Z"}, "outside working hours"),  # Sat
                ({"start": "2026-10-28T13:00:00Z", "end": "2026-10-28T14:00:00Z"}, "is in the past"),
                ({"start": "2027-01-04T13:00:00Z", "end": "2027-01-04T14:00:00Z"}, "the calendar books until"),
                ({"start": "2026-11-03T13:00:00Z", "end": "2026-11-03T22:00:00Z"}, "lasts 5 to 480 minutes"),
                ({"start": "2026-11-03T13:00:30Z", "end": "2026-11-03T14:00:00Z"}, "use whole minutes"),
                ({"start": "2026-11-03T13:00:00", "end": "2026-11-03T14:00:00"}, "has no UTC offset"),
                ({"start": "2026-11-03T13:00:00Z", "end": "2026-11-03T14:00:00Z", "attendees": ["x@y.com"]},
                 "is not in the calendar's directory"),
                ({"start": "2026-11-03T13:00:00Z", "end": "2026-11-03T14:00:00Z", "calendar": "hr"},
                 "unknown calendar")):
            await _refused(pool, "calendar.create_event", msg, **{**base, **args})
        ev = await _call(pool, "calendar.create_event", **base, start="2026-11-03T08:00:00-05:00",
                         end="2026-11-03T08:30:00-05:00")
        assert (ev["id"], ev["start_utc"], ev["organizer"]) == ("EV-0116", "2026-11-03T13:00:00Z", RANA)
        assert [a["email"] for a in ev["attendees"]] == [MAYA, RANA, TOM]            # the organiser is added
        await _refused(pool, "calendar.create_event", "conflict", **base, start="2026-11-03T13:15:00Z",
                       end="2026-11-03T13:45:00Z")                                  # clashes with the new one
    assert _state(comms, "calendar")["meta"]["sequences"]["event"] == 117


async def test_move_cancel_and_restore(factory, comms):
    async with MCPSessionPool(factory.declared, ["calendar"], comms.env()) as pool:
        await _refused(pool, "calendar.move_event", "outside working hours", id="EV-0107",
                       start="2026-11-02T12:00:00Z", end="2026-11-02T13:00:00Z")
        moved = await _call(pool, "calendar.move_event", id="EV-0107", start="2026-11-03T13:00:00Z",
                            end="2026-11-03T14:00:00Z")
        assert moved["start"] == "2026-11-03T17:00:00+04:00"
        assert {a["email"]: a["local_start"] for a in moved["attendees"]}[MAYA] == "2026-11-03T08:00:00-05:00"
        again = await _call(pool, "calendar.move_event", id="EV-0107", start="2026-11-03T13:15:00Z",
                            end="2026-11-03T14:00:00Z")                      # overlapping itself is no conflict
        assert again["end_utc"] == "2026-11-03T14:00:00Z"
        cancelled = await _call(pool, "calendar.cancel_event", id="EV-0107")
        assert cancelled["status"] == "cancelled"
        await _refused(pool, "calendar.cancel_event", "already cancelled", id="EV-0107")
        free = await _call(pool, "calendar.create_event", title="Other", start="2026-11-03T13:15:00Z",
                           end="2026-11-03T14:00:00Z", attendees=[TOM], calendar="procurement")   # slot is free now
        await _refused(pool, "calendar.move_event", "conflict", id="EV-0107", start="2026-11-03T13:15:00Z",
                       end="2026-11-03T14:00:00Z")
        restored = await _call(pool, "calendar.move_event", id="EV-0107", start="2026-11-04T13:00:00Z",
                               end="2026-11-04T14:00:00Z")
        assert restored["status"] == "confirmed" and free["id"] == "EV-0116"
        await _refused(pool, "calendar.move_event", "has already started", id="EV-0101",
                       start="2026-11-04T06:00:00Z", end="2026-11-04T06:30:00Z")
        await _refused(pool, "calendar.cancel_event", "has already started", id="EV-0101")


async def test_find_free_slots(factory, comms):
    week = {"start": "2026-11-02T00:00:00+04:00", "end": "2026-11-07T00:00:00+04:00"}
    async with MCPSessionPool(factory.declared, ["calendar"], comms.env()) as pool:
        got = await _call(pool, "calendar.find_free_slots", attendees=[RANA, TOM, MAYA], duration=60, window=week)
        # Monday's only overlap (13:00-14:00 UTC) clashes with the payroll sign-off; Tuesday's is free.
        assert [s["start_utc"] for s in got["slots"]] == ["2026-11-03T13:00:00Z", "2026-11-04T13:00:00Z",
                                                          "2026-11-05T13:00:00Z", "2026-11-06T13:00:00Z"]
        assert got["slots"][0]["local"] == {MAYA: "2026-11-03T08:00:00-05:00", RANA: "2026-11-03T17:00:00+04:00",
                                            TOM: "2026-11-03T13:00:00+00:00"} and not got["more"]
        monday = {"start": "2026-11-02T00:00:00+04:00", "end": "2026-11-03T00:00:00+04:00"}
        got = await _call(pool, "calendar.find_free_slots", attendees=[RANA, TOM, ARJUN], duration=45, window=monday)
        assert got["slots"][0]["start_utc"] == "2026-11-02T11:15:00Z"
        assert got["slots"][0]["local"][ARJUN] == "2026-11-02T16:45:00+05:30"     # half-hour offset
        many = await _call(pool, "calendar.find_free_slots", attendees=[RANA], duration=15, window=week)
        assert len(many["slots"]) == 10 and many["more"]
        past = await _call(pool, "calendar.find_free_slots", attendees=[RANA], duration=60,
                           window={"start": "2026-10-29T00:00:00Z", "end": "2026-10-29T08:00:00Z"})
        # from now (06:00 UTC) on; then the budget check-in (07:00-07:30) and the window's end leave no other hour
        assert [x["start_utc"] for x in past["slots"]] == ["2026-10-29T06:00:00Z"]
        for args, msg in (({"duration": 10}, "duration is a whole number"), ({"duration": True}, "duration is"),
                          ({"window": {"start": week["start"]}}, 'window is {"start"'),
                          ({"window": {"start": "2026-11-02T00:00:00Z", "end": "2026-11-30T00:00:00Z"}},
                           "at most 14 days"),
                          ({"attendees": []}, "non-empty list"), ({"attendees": ["nobody@x.com"]}, "not in the")):
            await _refused(pool, "calendar.find_free_slots", msg,
                           **{"attendees": [RANA], "duration": 30, "window": week, **args})


def test_seed_zone_tables_match_the_tz_database():
    """The seed's offsets (written from the 2026-2027 rules, so the server needs no tz database) agree with
    zoneinfo at every hour of both years, where this Python has a tz database."""
    zoneinfo = pytest.importorskip("zoneinfo")
    state = json.loads((SEEDS / "calendar.json").read_text())
    try:
        zones = {z: zoneinfo.ZoneInfo(z) for z in state["zones"]}
    except zoneinfo.ZoneInfoNotFoundError:
        pytest.skip("no tz database here")
    t = datetime(2026, 1, 1, tzinfo=UTC)
    from datetime import timedelta
    while t.year < 2028:
        for name, zi in zones.items():
            assert cal.offset(state, name, t) == t.astimezone(zi).utcoffset(), (name, t)
        t += timedelta(hours=1)


def test_offsets_change_at_the_transition_instant():
    state = json.loads((SEEDS / "calendar.json").read_text())
    at = lambda s: datetime.fromisoformat(s)
    assert cal.local_text(state, "Europe/London", at("2026-10-25T00:59:00Z")) == "2026-10-25T01:59:00+01:00"
    assert cal.local_text(state, "Europe/London", at("2026-10-25T01:00:00Z")) == "2026-10-25T01:00:00+00:00"
    assert cal.local_text(state, "America/New_York", at("2026-11-01T05:59:00Z")) == "2026-11-01T01:59:00-04:00"
    assert cal.local_text(state, "America/New_York", at("2026-11-01T06:00:00Z")) == "2026-11-01T01:00:00-05:00"
    assert cal.local_text(state, "Asia/Kolkata", at("2026-11-02T11:15:00Z")) == "2026-11-02T16:45:00+05:30"


# ------------------------------------------------------------------ per-run state

async def test_states_are_reset_for_every_run(factory):
    """Canary: the messages the first run sends must not exist when the second run starts (they would make the
    second run's ids M-0005...), and the seed files themselves are never written."""
    task, recipe = factory.tasks.get("out-01"), factory.recipes["v2-outbox"]
    seeds = list(task.env_seed.state_seeds(task.source_dir).values())
    before = hash_paths(seeds)
    first = await run_one(factory, task, recipe, "scripted/replay", "scripted")
    second = await run_one(factory, task, recipe, "scripted/replay", "scripted", seed=1)
    for rec in (first, second):
        state = next(g for g in rec.grades if g["grader_id"] == "state_diff")
        assert state["detail"] == ("+outbox deliveries/D-0004, deliveries/D-0005, deliveries/D-0006, "
                                   "notifications/N-0002, sent/M-0002, sent/M-0003, sent/M-0004; ~outbox meta")
    assert hash_paths(seeds) == before


def test_reset_replaces_or_removes_the_previous_runs_states(factory):
    with LocalSandbox("comms-reset") as sb:
        sb.reset(factory.tasks.get("cal-01"))
        sb.state_path("outbox").write_text("{}")
        sb.reset(factory.tasks.get("cal-01"))
        assert _state(sb, "outbox")["sent"]                             # a fresh copy of the seed again
        sb.reset(factory.tasks.get("cal-02"))                           # seeds only the calendar
        assert sb.state_path("calendar").is_file() and not sb.state_path("outbox").exists()
        env = sb.env()
        assert env["HF_OUTBOX"] == str(sb.db_path.parent / "outbox.json")         # next to the database, like biz
        assert env["HF_CALENDAR"] == str(sb.db_path.parent / "calendar.json")
        assert not sb.state_path("calendar").is_relative_to(sb.workspace)          # fs can't edit it


def test_a_seed_must_be_a_json_object(factory, tmp_path):
    task = factory.tasks.get("cal-02").model_copy(deep=True)
    bad = tmp_path / "bad.json"
    bad.write_text("[1]")
    task.env_seed.server_state = {"calendar": str(bad)}
    with LocalSandbox("comms-bad") as sb, pytest.raises(ValueError, match="server_state.calendar seed .* not a JSON"):
        sb.reset(task)


def test_state_diff_reports_records_per_state(tmp_path):
    db = tmp_path / "state" / "task.sqlite"
    db.parent.mkdir()
    insp = StateInspector(tmp_path / "workspace", db)
    assert insp.capture().states == {}
    doc = {"meta": {"now": "x"}, "events": {"EV-1": {"status": "confirmed"}, "EV-2": {"status": "confirmed"}}}
    db.with_name("calendar.json").write_text(json.dumps(doc))
    before = insp.capture()
    doc["events"]["EV-1"]["status"] = "cancelled"
    del doc["events"]["EV-2"]
    doc["events"]["EV-3"] = {"status": "confirmed"}
    db.with_name("calendar.json").write_text(json.dumps(doc))
    db.with_name("outbox.json").write_text(json.dumps({"sent": {"M-1": {"to": []}}}))
    d = StateInspector.diff(before, insp.capture())
    assert (d.state_added, d.state_removed, d.state_changed) == (
        ["outbox:sent/M-1", "calendar:events/EV-3"], ["calendar:events/EV-2"], ["calendar:events/EV-1"])
    assert d.summary() == "+outbox sent/M-1; +calendar events/EV-3; -calendar events/EV-2; ~calendar events/EV-1"
    assert StateInspector.diff(before, before).is_empty


def test_end_states_keep_the_json_states(factory, tmp_path):
    """GRD-04: a re-grade reads the same states the run left (biz included)."""
    from harness_factory.runner.endstate import load_endstate, save_endstate
    ws, db = tmp_path / "w", tmp_path / "s" / "task.sqlite"
    ws.mkdir()
    db.parent.mkdir()
    for name in ("biz.json", "outbox.json", "calendar.json"):
        db.with_name(name).write_text(json.dumps({"name": name}))
    after = StateInspector(ws, db).capture()
    ctx = GradeContext(task=factory.tasks.get("cal-01"), result=_result(), before=EnvState(), after=after,
                       diff=StateInspector.diff(EnvState(), after), workspace=ws, db_path=db, spans=[], max_steps=1)
    bundle = save_endstate(tmp_path / "run.jsonl", ctx)
    w2, db2, _ = load_endstate(bundle and __import__("pathlib").Path(bundle), tmp_path / "out")
    again = StateInspector(w2, db2).capture()
    assert again.biz == {"name": "biz.json"} and again.states == after.states and len(again.states) == 2


def _result():
    from harness_factory.runner.spec import RunKey
    return RunResult(run_key=RunKey(task_version="cal-01@1", task_hash="x", recipe_hash="y", canonical_model="m",
                                    provider="p"), output="", finished=True, scored=True)


# ------------------------------------------------------------------ checks

DOC = {"events": {"EV-1": {"status": "confirmed", "attendees": ["a", "b"], "n": 1, "flag": True},
                  "EV-2": {"status": "cancelled", "attendees": ["a"], "n": 2, "flag": False}}}


def _grade(*checks, states=None) -> dict[str, bool]:
    weight = 1 / len(checks)
    cs = [WeightedCheck(id=f"c{i}", weight=weight, **c) for i, c in enumerate(checks)]
    cs[-1].weight = round(1 - weight * (len(cs) - 1), 9)
    task = __import__("types").SimpleNamespace(oracle=Oracle(checks=cs))
    after = EnvState(states={"calendar": DOC} if states is None else states, biz={"x": {"y": 1}})
    ctx = GradeContext(task=task, result=RunResult.model_construct(), before=EnvState(), after=after,
                       diff=StateInspector.diff(EnvState(), EnvState()), workspace=None, db_path=None, spans=[],
                       max_steps=1)
    return {c.id: c.passed for c in StateDiffGrader().grade(ctx).checks}


def test_state_equals_is_strict_on_type():
    got = _grade({"kind": "state_equals", "path": "calendar:/events/EV-1/status", "text": "confirmed"},
                 {"kind": "state_equals", "path": "calendar:/events/EV-1/n", "text": "1.0"},       # numbers by value
                 {"kind": "state_equals", "path": "calendar:/events/EV-1/flag", "text": "1"},      # true is not 1
                 {"kind": "state_equals", "path": "calendar:/events/EV-1/n", "text": '"1"'},       # "1" is not 1
                 {"kind": "state_equals", "path": "calendar:/events/EV-1/attendees", "text": '["b", "a"]'},  # order
                 {"kind": "state_equals", "path": "calendar:/events/EV-9", "text": "null"},        # missing
                 {"kind": "state_equals", "path": "calendar:/events/EV-2/status", "text": "confirmed",
                  "negate": True},
                 {"kind": "state_equals", "path": "biz:/x/y", "text": "1"},                        # biz too
                 {"kind": "state_equals", "path": "outbox:/sent", "text": "{}"})                   # not seeded
    assert list(got.values()) == [True, True, False, False, False, False, True, True, False]


def test_state_count_counts_matching_records():
    got = _grade({"kind": "state_count", "path": "calendar:/events", "text": "2"},
                 {"kind": "state_count", "path": "calendar:/events", "text": "1",
                  "query": '{"status": "confirmed", "attendees": ["a", "b"]}'},
                 {"kind": "state_count", "path": "calendar:/events", "text": "0", "query": '{"flag": 1}'},
                 {"kind": "state_count", "path": "calendar:/events/EV-1/status", "text": "1"},  # not a collection
                 {"kind": "state_count", "path": "outbox:/sent", "text": "0"})                  # no outbox state
    assert list(got.values()) == [True, True, True, False, False]


def test_state_checks_validate_their_fields():
    for bad, msg in (({"kind": "state_equals", "path": "calendar:/a"}, "needs text"),
                     ({"kind": "state_equals", "path": "/events/EV-1", "text": "x"}, "'<state>:<JSON pointer>'"),
                     ({"kind": "state_equals", "path": "mail:/sent", "text": "x"}, "state one of biz, outbox"),
                     ({"kind": "state_equals", "path": "calendar:events", "text": "x"}, "JSON pointer"),
                     ({"kind": "state_equals", "path": "calendar:/e", "text": "x", "query": "{}"}, "for state_count"),
                     ({"kind": "state_count", "path": "calendar:/e", "text": "two"}, "whole number"),
                     ({"kind": "state_count", "path": "calendar:/e", "text": "1", "query": "[1]"}, "JSON object")):
        with pytest.raises(ValueError, match=msg):
            WeightedCheck(id="x", weight=1, **bad)
    with pytest.raises(ValueError, match="unknown state mail"):
        EnvSeed(server_state={"mail": "x.json"})
    with pytest.raises(ValueError, match="state checks on a state the task doesn't seed: c \\(outbox\\)"):
        Task(id="t", domain="ops", instruction="x", allowed_tools=[], env_seed=EnvSeed(server_state={"calendar": "c"}),
             oracle=Oracle(checks=[WeightedCheck(id="c", kind="state_count", weight=1, path="outbox:/sent",
                                                 text="1")]))


# ------------------------------------------------------------------ runs, policy, plans and identity

async def test_the_tasks_succeed_end_to_end(factory):
    for task_id, recipe in (("cal-01", "v2-outbox"), ("cal-02", "v2-outbox"), ("out-01", "v2-outbox"),
                            ("cal-02", "v2-calendar")):
        rec = await run_one(factory, factory.tasks.get(task_id), factory.recipes[recipe], "scripted/replay",
                            "scripted")
        assert rec.status.value == "SUCCESS", (task_id, recipe, rec.grades)
        assert rec.composite == pytest.approx(1.0) and not rec.violations


async def test_under_v2_base_policy_every_send_is_refused(factory):
    """IRREVERSIBLE: DENY (v2-base's policy, and ASK until the approval gate): the outbox opens, but no send
    reaches it, so nothing is sent and out-01 scores 0. This is why v2-outbox grants IRREVERSIBLE."""
    deny = factory.recipes["v2-outbox"].model_copy(deep=True)
    deny.policies.permission_mode["IRREVERSIBLE"] = "DENY"
    rec = await run_one(factory, factory.tasks.get("out-01"), deny, "scripted/replay", "scripted")
    denied = sorted({v.detail for v in rec.violations if v.kind == "policy_denied"})
    assert denied == ["outbox.notify (IRREVERSIBLE)", "outbox.send_email (IRREVERSIBLE)"]
    state = next(g for g in rec.grades if g["grader_id"] == "state_diff")
    assert state["detail"] == "no change" and rec.scores.outcome == 0 and rec.status.value != "SUCCESS"


async def test_cal_01_and_out_01_fit_the_v3_stage_caps(factory):
    """CLAUDE.md: a reference fits V3 (≤3 reads before the first write, ≤5 calls after)."""
    base = factory.recipes["v3-staged"]
    reads = ["calendar.list_events", "calendar.get_event", "calendar.find_free_slots", "outbox.list_templates",
             "outbox.get_template", "outbox.list_sent"]
    stages = [s.model_copy(update={"tools": s.tools + reads}) if s.id != "act" else s for s in base.workflow.stages]
    v3 = base.model_copy(update={
        "id": "v3-comms-test", "tier": "T3",
        "policies": base.policies.model_copy(update={"permission_mode": {"READ": "AUTO", "REVERSIBLE": "AUTO",
                                                                         "IRREVERSIBLE": "AUTO"}}),
        "tools": base.tools.model_copy(update={"tool_ids": [*base.tools.tool_ids, "calendar.*", "outbox.*"],
                                               "mcp_servers": [*base.tools.mcp_servers, "calendar", "outbox"]}),
        "workflow": base.workflow.model_copy(update={"stages": stages})})
    for task_id in ("cal-01", "out-01"):
        rec = await run_one(factory, factory.tasks.get(task_id), v3, "scripted/replay", "scripted")
        assert rec.status.value == "SUCCESS", (task_id, rec.grades)


def test_campaigns_plan_the_tasks_only_with_recipes_that_open_their_servers(factory):
    d, r = factory.declared, factory.recipes
    cal1, cal2, out1 = (factory.tasks.get(t) for t in ("cal-01", "cal-02", "out-01"))
    assert not any(reaches(r[x], t, d) for x in ("v2-base", "v2-biz") for t in (cal1, cal2, out1))
    assert reaches(r["v2-calendar"], cal2, d) and not reaches(r["v2-calendar"], cal1, d)
    assert all(reaches(r["v2-outbox"], t, d) for t in (cal1, cal2, out1))
    specs = plan(factory, ["v2-base", "v2-calendar", "v2-outbox"], "scripted", ["scripted/replay"],
                 task_ids=["cal-01", "cal-02", "out-01"])
    assert sorted((s.task_id, s.recipe_id) for s in specs) == [
        ("cal-01", "v2-outbox"), ("cal-02", "v2-calendar"), ("cal-02", "v2-outbox"), ("out-01", "v2-outbox")]
    assert task_class(out1, d) == "UNKNOWN_PATH|IRREVERSIBLE|R3" and task_class(cal2, d) == "KNOWN_PATH|REVERSIBLE|R1"


def test_matrix_recipes_change_what_they_say(factory):
    r = factory.recipes
    base, calendar, outbox = (r[x].model_dump() for x in ("v2-base", "v2-calendar", "v2-outbox"))
    assert sorted(k for k in base if base[k] != calendar[k]) == ["base", "id", "tools"]
    assert sorted(k for k in base if base[k] != outbox[k]) == ["base", "id", "policies", "tier", "tools"]
    assert outbox["policies"]["permission_mode"]["IRREVERSIBLE"] == "AUTO"
    irreversible = [ref for ref in factory.declared.all_refs() if r["v2-outbox"].tools.allows(ref)
                    and ref.split(".")[0] in r["v2-outbox"].tools.mcp_servers
                    and factory.declared.spec(ref).side_effect.value == "IRREVERSIBLE"]
    assert sorted(irreversible) == ["outbox.notify", "outbox.send_email"]       # nothing else it may now do


def test_adding_the_servers_moved_no_existing_identity(factory):
    """Values from before these servers existed (also checked catalog-wide against the base branch in the PR)."""
    d = factory.declared
    assert d.env_hash(factory.recipes["v2-base"].tools.mcp_servers, "openai/gpt-oss-120b") == "a55c2fb739f1"
    assert factory.recipes["v2-base"].content_hash() == "79abd03818943f65"
    assert factory.recipes["v2-biz"].content_hash() == "9d10ffca30705db3"
    assert factory.tasks.get("fs-01").content_hash() == "f88f6a779f5f0dfc"
    assert factory.tasks.get("biz-01").content_hash() == "b70c828fca5a691c"
