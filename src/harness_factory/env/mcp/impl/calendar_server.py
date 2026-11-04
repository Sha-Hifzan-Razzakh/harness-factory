"""calendar MCP server: team calendars over the run's state file (HF_CALENDAR).

The state is one JSON document, copied from the task's seed (`env_seed.server_state.calendar`) into the run's state
folder before every run, like the biz server's. Graders read the file after the run (`state_equals` /
`state_count` checks on "calendar:/..."). It is offline and deterministic: ids come from a sequence kept in the
state, and "now" is the seed's `meta.now`, never the clock.

Time is explicit everywhere:
- every time a tool takes must carry its UTC offset ("2026-11-03T17:00:00+04:00" or "...Z"); a time without one
  is refused rather than guessed;
- events are stored in UTC ("2026-11-03T13:00:00Z") and shown in the business timezone (`meta.timezone`), with
  each attendee's local time next to them;
- timezones come from the seed (`zones`: name -> offsets from given UTC instants, daylight saving included), not
  from the host's tz database: the standard library has none on Windows or in slim images, and a run must not
  depend on the host's tzdata version.

A real calendar refuses, and so does this one (as ToolError): a time in the past or beyond the booking horizon, an
attendee outside their working hours (in their own timezone), an attendee who is already busy, an unknown attendee
or calendar.

Side effects (catalog/tools.yaml): every write can be undone here, so all are REVERSIBLE: a moved event moves back,
a cancelled event is restored by moving it, and a created one is cancelled. The calendar sends no invitations;
telling people is the outbox server's job.

Standard library only, so it runs inside the sandbox image.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError  # message reaches the model; ValueError would be masked

app = MCPServer("calendar", instructions="Team calendars: events, attendees' working hours and free slots. Every "
                                         "time carries its UTC offset (e.g. 2026-11-03T17:00:00+04:00).")

DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MIN_MINUTES, MAX_MINUTES = 5, 8 * 60
MAX_LIST_DAYS = 31
MAX_SLOT_WINDOW_DAYS = 14
MAX_SLOTS = 10
MAX_TITLE = 200
MAX_DESCRIPTION = 2000


# ------------------------------------------------------------------ state

def _path() -> Path:
    return Path(os.environ.get("HF_CALENDAR", ""))


def _load() -> dict[str, Any]:
    p = _path()
    if not os.environ.get("HF_CALENDAR") or not p.is_file():
        raise ToolError("this task has no calendar to work with (no calendar state was seeded)")
    return json.loads(p.read_text())


def _save(state: dict[str, Any]) -> None:
    p = _path()
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=1, ensure_ascii=False) + "\n")
    os.replace(tmp, p)                      # a reader never sees half a document


# ------------------------------------------------------------------ time

def utc_text(t: datetime) -> str:
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(text: str) -> datetime:
    return datetime.fromisoformat(text).astimezone(UTC)


def instant(value: Any, what: str) -> datetime:
    """A time the model gave: ISO 8601 with a UTC offset, whole minutes. Returned in UTC."""
    if not isinstance(value, str):
        raise ToolError(f"{what} must be a time as text, e.g. 2026-11-03T17:00:00+04:00")
    try:
        t = datetime.fromisoformat(value.strip())
    except ValueError:
        raise ToolError(f"{what} {value!r} is not an ISO 8601 time, e.g. 2026-11-03T17:00:00+04:00") from None
    if t.tzinfo is None or t.utcoffset() is None:
        raise ToolError(f"{what} {value!r} has no UTC offset; say which zone it is in, e.g. "
                        f"{value.strip()}+04:00 for Asia/Dubai or {value.strip()}Z for UTC")
    if t.second or t.microsecond:
        raise ToolError(f"{what} {value!r}: use whole minutes")
    return t.astimezone(UTC)


def offset(state: dict, zone: str, t: datetime) -> timedelta:
    """The zone's UTC offset at instant t, from the seed's zone table (the last entry that starts at or before t)."""
    table = state["zones"].get(zone)
    if not table:
        raise ToolError(f"unknown timezone {zone!r}")
    current = None
    for entry in table:
        if parse_utc(entry["from"]) <= t:
            current = entry["offset"]
    if current is None:
        raise ToolError(f"{utc_text(t)} is before the calendar's timezone data for {zone}")
    sign = -1 if current.startswith("-") else 1
    hh, mm = current.lstrip("+-").split(":")
    return sign * timedelta(hours=int(hh), minutes=int(mm))


def local(state: dict, zone: str, t: datetime) -> datetime:
    off = offset(state, zone, t)
    return (t + off).replace(tzinfo=timezone(off))


def local_text(state: dict, zone: str, t: datetime) -> str:
    return local(state, zone, t).isoformat()


def _now(state: dict) -> datetime:
    return parse_utc(state["meta"]["now"])


def _biz_zone(state: dict) -> str:
    return state["meta"]["timezone"]


# ------------------------------------------------------------------ rules

def _person(state: dict, email: Any) -> tuple[str, dict]:
    key = email.strip().lower() if isinstance(email, str) else email
    p = state["people"].get(key) if isinstance(key, str) else None
    if p is None:
        raise ToolError(f"{email!r} is not in the calendar's directory, so their timezone and working hours are "
                        f"unknown; known: {', '.join(state['people'])}")
    return key, p


def _attendees(state: dict, value: Any) -> list[str]:
    if not isinstance(value, list) or not value or not all(isinstance(a, str) for a in value):
        raise ToolError("attendees must be a non-empty list of email addresses")
    return sorted({_person(state, a)[0] for a in value})


def _hours_text(state: dict, email: str) -> str:
    p = state["people"][email]
    wh = p["working_hours"]
    return f"{'/'.join(wh['days'])} {wh['start']}-{wh['end']} {p['timezone']}"


def outside_hours(state: dict, email: str, start: datetime, end: datetime) -> str | None:
    """Why [start, end) is outside the person's working hours in their own timezone, or None if it is inside."""
    p = state["people"][email]
    wh, zone = p["working_hours"], p["timezone"]
    a, last = local(state, zone, start), local(state, zone, end - timedelta(minutes=1))   # its last minute
    inside = (a.date() == last.date() and DAYS[a.weekday()] in wh["days"] and a.strftime("%H:%M") >= wh["start"]
              and last.strftime("%H:%M") < wh["end"])
    if inside:
        return None
    return (f"{email} works {_hours_text(state, email)}; this is {local_text(state, zone, start)} to "
            f"{local_text(state, zone, end)} there")


def busy_with(state: dict, email: str, start: datetime, end: datetime, skip: str | None = None) -> dict | None:
    """The first confirmed event (other than skip) the person attends that overlaps [start, end)."""
    for ev in sorted(state["events"].values(), key=lambda e: e["start"]):
        if ev["id"] == skip or ev["status"] != "confirmed" or email not in ev["attendees"]:
            continue
        if parse_utc(ev["start"]) < end and start < parse_utc(ev["end"]):
            return ev
    return None


def check_slot(state: dict, attendees: list[str], start: datetime, end: datetime, skip: str | None = None) -> None:
    """Everything a booking must satisfy; ToolError with the first reason it doesn't."""
    zone = _biz_zone(state)
    minutes = (end - start) / timedelta(minutes=1)
    if end <= start:
        raise ToolError("end must be after start")
    if not MIN_MINUTES <= minutes <= MAX_MINUTES:
        raise ToolError(f"an event lasts {MIN_MINUTES} to {MAX_MINUTES} minutes, not {minutes:g}")
    if start < _now(state):
        raise ToolError(f"{local_text(state, zone, start)} is in the past (now is "
                        f"{local_text(state, zone, _now(state))} {zone})")
    horizon = parse_utc(state["meta"]["horizon_end"])
    if end > horizon:
        raise ToolError(f"the calendar books until {local_text(state, zone, horizon)} {zone}")
    for email in attendees:
        why = outside_hours(state, email, start, end)
        if why:
            raise ToolError(f"outside working hours: {why}")
    for email in attendees:
        ev = busy_with(state, email, start, end, skip)
        if ev is not None:
            raise ToolError(f"conflict: {email} is busy from {local_text(state, zone, parse_utc(ev['start']))} to "
                            f"{local_text(state, zone, parse_utc(ev['end']))} ({ev['id']})")


# ------------------------------------------------------------------ views

def _times(state: dict, ev: dict) -> dict:
    zone, s, e = _biz_zone(state), parse_utc(ev["start"]), parse_utc(ev["end"])
    return {"start": local_text(state, zone, s), "end": local_text(state, zone, e), "timezone": zone,
            "start_utc": ev["start"], "end_utc": ev["end"]}


def _summary(state: dict, ev: dict) -> dict:
    return {"id": ev["id"], "title": ev["title"], "calendar": ev["calendar"], **_times(state, ev),
            "organizer": ev["organizer"], "attendees": ev["attendees"], "status": ev["status"]}


def _detail(state: dict, ev: dict) -> dict:
    s, e = parse_utc(ev["start"]), parse_utc(ev["end"])
    people = [{"email": a, "name": state["people"][a]["name"], "timezone": state["people"][a]["timezone"],
               "local_start": local_text(state, state["people"][a]["timezone"], s),
               "local_end": local_text(state, state["people"][a]["timezone"], e),
               "working_hours": _hours_text(state, a)} for a in ev["attendees"]]
    return {**_summary(state, ev), "attendees": people, "description": ev.get("description", "")}


def _event(state: dict, eid: Any) -> dict:
    ev = state["events"].get(eid) if isinstance(eid, str) else None
    if ev is None:
        raise ToolError(f"unknown event id {eid!r}")
    return ev


def _window(state: dict, start: Any, end: Any, max_days: int) -> tuple[datetime, datetime]:
    a, b = instant(start, "start"), instant(end, "end")
    if b <= a:
        raise ToolError("end must be after start")
    if b - a > timedelta(days=max_days):
        raise ToolError(f"the window is at most {max_days} days")
    return a, b


# ------------------------------------------------------------------ tools

@app.tool()
def list_events(start: str, end: str, calendar: str | None = None) -> dict:
    """List confirmed events that overlap [start, end), oldest first, optionally on one calendar. start and end
    carry their UTC offset; the window is at most 31 days. Times are shown in the business timezone."""
    state = _load()
    a, b = _window(state, start, end, MAX_LIST_DAYS)
    if calendar is not None and calendar not in state["calendars"]:
        raise ToolError(f"unknown calendar {calendar!r}; calendars: {', '.join(state['calendars'])}")
    rows = [_summary(state, ev) for ev in sorted(state["events"].values(), key=lambda e: e["start"])
            if ev["status"] == "confirmed" and (calendar is None or ev["calendar"] == calendar)
            and parse_utc(ev["start"]) < b and a < parse_utc(ev["end"])]
    zone = _biz_zone(state)
    return {"now": local_text(state, zone, _now(state)), "timezone": zone, "events": rows}


@app.tool()
def get_event(id: str) -> dict:
    """Get one event by id (e.g. "EV-0107"), with each attendee's timezone, local time and working hours."""
    state = _load()
    return _detail(state, _event(state, id))


@app.tool()
def create_event(title: str, start: str, end: str, attendees: list[str], calendar: str,
                 description: str = "") -> dict:
    """Book an event on a calendar. start and end carry their UTC offset. attendees are email addresses from the
    directory; you (the organizer) are added. Refused if it is in the past, outside an attendee's working hours,
    or clashes with an attendee's event. Returns the event."""
    state = _load()
    if calendar not in state["calendars"]:
        raise ToolError(f"unknown calendar {calendar!r}; calendars: {', '.join(state['calendars'])}")
    if not isinstance(title, str) or not title.strip() or len(title) > MAX_TITLE:
        raise ToolError(f"title must be non-empty text of at most {MAX_TITLE} characters")
    if not isinstance(description, str) or len(description) > MAX_DESCRIPTION:
        raise ToolError(f"description must be text of at most {MAX_DESCRIPTION} characters")
    me = state["meta"]["user"]
    people = sorted({*_attendees(state, attendees), me})
    a, b = instant(start, "start"), instant(end, "end")
    check_slot(state, people, a, b)
    seqs = state["meta"]["sequences"]
    eid = f"EV-{int(seqs['event']):04d}"
    seqs["event"] = int(seqs["event"]) + 1
    ev = {"id": eid, "calendar": calendar, "title": title.strip(), "start": utc_text(a), "end": utc_text(b),
          "organizer": me, "attendees": people, "status": "confirmed", "description": description.strip()}
    state["events"][eid] = ev
    _save(state)
    return _detail(state, ev)


@app.tool()
def move_event(id: str, start: str, end: str) -> dict:
    """Move an event to a new start and end (with their UTC offset), keeping its attendees. The same rules as
    booking apply. Moving a cancelled event restores it at the new time. Returns the event."""
    state = _load()
    ev = _event(state, id)
    if ev["status"] == "confirmed" and parse_utc(ev["start"]) <= _now(state):
        raise ToolError(f"{id} has already started; it can't be moved")
    a, b = instant(start, "start"), instant(end, "end")
    check_slot(state, ev["attendees"], a, b, skip=id)
    ev["start"], ev["end"], ev["status"] = utc_text(a), utc_text(b), "confirmed"
    _save(state)
    return _detail(state, ev)


@app.tool()
def cancel_event(id: str) -> dict:
    """Cancel an event that hasn't started. Attendees are not told (send them a message). A cancelled event can
    be restored with move_event. Returns the event."""
    state = _load()
    ev = _event(state, id)
    if ev["status"] == "cancelled":
        raise ToolError(f"{id} is already cancelled")
    if parse_utc(ev["start"]) <= _now(state):
        raise ToolError(f"{id} has already started; it can't be cancelled")
    ev["status"] = "cancelled"
    _save(state)
    return _detail(state, ev)


@app.tool()
def find_free_slots(attendees: list[str], duration: int, window: dict[str, str]) -> dict:
    """Find times when every attendee is free and inside their own working hours. duration is in minutes;
    window is {"start": ..., "end": ...} with UTC offsets, at most 14 days. Returns the earliest slots (at most
    10), each in the business timezone, in UTC and in every attendee's local time."""
    state = _load()
    people = _attendees(state, attendees)
    if isinstance(duration, bool) or not isinstance(duration, int) or not 15 <= duration <= MAX_MINUTES:
        raise ToolError(f"duration is a whole number of minutes from 15 to {MAX_MINUTES}")
    if not isinstance(window, dict) or set(window) != {"start", "end"}:
        raise ToolError('window is {"start": <time>, "end": <time>}, each with its UTC offset')
    a, b = _window(state, window["start"], window["end"], MAX_SLOT_WINDOW_DAYS)
    step = timedelta(minutes=int(state["meta"]["slot_minutes"]))
    t = max(a, _now(state))
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    t = epoch + -((epoch - t) // step) * step                 # round up to the slot grid (UTC)
    length, zone, horizon = timedelta(minutes=duration), _biz_zone(state), parse_utc(state["meta"]["horizon_end"])
    slots, more = [], False
    while t + length <= min(b, horizon):
        if all(outside_hours(state, p, t, t + length) is None and busy_with(state, p, t, t + length) is None
               for p in people):
            if len(slots) == MAX_SLOTS:
                more = True
                break
            slots.append({"start": local_text(state, zone, t), "end": local_text(state, zone, t + length),
                          "start_utc": utc_text(t), "end_utc": utc_text(t + length),
                          "local": {p: local_text(state, state["people"][p]["timezone"], t) for p in people}})
        t += step
    return {"timezone": zone, "duration": duration, "slots": slots, "more": more}


if __name__ == "__main__":
    app.run()
