"""Web research tasks (ADR 0018): each trap must cost points, grounding means fetching the page the answer is on.

The wrong answers are the ones the recorded corpora set up: a retired documentation page that ranks first, a launch
post with an older figure, a press release the operator later corrected, and a newspaper article that is newer
than the release it repeats. The catalog-wide hedge and do-nothing tests in tests/test_task_traps.py pick these
TRAPS up too."""

from __future__ import annotations

import pytest
from test_task_traps import call, failed_checks, say

DOCS = "https://docs.relaymail.example"
PRESS = "https://press.saltmarsh-ferries.example/news"
GAZETTE = "https://coastal-gazette.example"
BRIEF = call("fs.read_file", path="brief.md")


def search(query: str) -> dict:
    return call("web.search", query=query)


def fetch(url: str) -> dict:
    return call("web.fetch", url=url)


RATE = search("Relaymail API rate limit per key requests per minute")
KESSICK = search("Kessick first passenger sailing date")

TRAPS = [
    # task, trap, script, checks that must fail, recipe
    ("web-01", "trusted the retired v1 page, which ranks first",
     [RATE, fetch(f"{DOCS}/v1/rate-limits"), say(f"100 requests per minute ({DOCS}/v1/rate-limits)")],
     {"answer", "source", "grounded"}, "v2-web"),
    ("web-01", "took the v2 launch post's figure",
     [RATE, fetch("https://relaymail.example/blog/introducing-v2"),
      say("300 requests per minute (https://relaymail.example/blog/introducing-v2)")],
     {"answer", "source", "grounded"}, "v2-web"),
    ("web-01", "took the changelog's older entry",
     [RATE, fetch(f"{DOCS}/changelog"), say(f"300 ({DOCS}/changelog)")], {"answer"}, "v2-web"),
    ("web-01", "multiplied the v1 per-key limit by three keys",
     [RATE, fetch(f"{DOCS}/v1/rate-limits"), say(f"300 requests per minute ({DOCS}/v1/rate-limits)")],
     {"answer", "source"}, "v2-web"),
    ("web-02", "took the newest article, which repeats the June date",
     [KESSICK, fetch(f"{GAZETTE}/kessick-ferry-september"), say(f"2026-09-15 ({GAZETTE}/kessick-ferry-september)")],
     {"answer", "source", "grounded"}, "v2-web"),
    ("web-02", "stopped at the June update",
     [KESSICK, fetch(f"{PRESS}/kessick-route-update"), say(f"2026-09-15 ({PRESS}/kessick-route-update)")],
     {"answer", "source", "grounded"}, "v2-web"),
    ("web-02", "trusted the news index, fetched before the September release",
     [fetch(PRESS), fetch(f"{PRESS}/kessick-route-update"), say(f"2026-09-15 ({PRESS}/kessick-route-update)")],
     {"answer", "source", "grounded"}, "v2-web"),
    ("web-02", "took the first announcement",
     [KESSICK, fetch(f"{PRESS}/kessick-route-announced"), say(f"2026-07-01 ({PRESS}/kessick-route-announced)")],
     {"answer", "source", "grounded"}, "v2-web"),
    ("web-02", "gave the freight start",
     [KESSICK, fetch(f"{PRESS}/kessick-sea-trials-complete"), say(f"2026-08-01 ({PRESS}/kessick-sea-trials-complete)")],
     {"answer"}, "v2-web"),
]


@pytest.mark.parametrize(("task_id", "trap", "script", "must_fail", "recipe"), TRAPS,
                         ids=[f"{t[0]}: {t[1]}" for t in TRAPS])
async def test_trap_costs_points(factory, task_id, trap, script, must_fail, recipe):
    status, failed = await failed_checks(factory, task_id, *script, recipe=recipe)
    assert status != "SUCCESS"
    assert must_fail <= failed, f"{trap}: expected {must_fail} to fail, failed {failed}"


# The right answer without opening the page it is on: a search snippet, a 404, or another page.
GROUNDING_TRAPS = [
    ("web-01", "answered from the search snippets", [RATE, say(f"600 ({DOCS}/v2/rate-limits)")]),
    ("web-01", "fetched the retired page, answered from the snippet",
     [RATE, fetch(f"{DOCS}/v1/rate-limits"), say(f"600 ({DOCS}/v2/rate-limits)")]),
    ("web-01", "a link that is a 404", [fetch(f"{DOCS}/v1/limits-faq"), say(f"600 ({DOCS}/v2/rate-limits)")]),
    ("web-02", "answered from the search snippets", [KESSICK, say(f"2026-09-29 ({PRESS}/kessick-sea-trials-complete)")]),
]


@pytest.mark.parametrize(("task_id", "trap", "script"), GROUNDING_TRAPS, ids=[f"{t[0]}: {t[1]}" for t in GROUNDING_TRAPS])
async def test_grounding_means_fetching_the_page(factory, task_id, trap, script):
    status, failed = await failed_checks(factory, task_id, *script, recipe="v2-web")
    assert status != "SUCCESS"
    assert "grounded" in failed and "answer" not in failed, f"{trap}: failed {failed}"


# The page reached in other ways a model may well use: still grounded. (`touches` reads the url argument; a
# redirect alias is listed in the task, a scheme, trailing slash or fragment is covered by the globs.)
GROUNDED = [
    ("web-01", [BRIEF, fetch(f"{DOCS}/rate-limits"), say(f"600 ({DOCS}/v2/rate-limits)")]),       # via the redirect
    ("web-01", [fetch("http://docs.relaymail.example/v2/rate-limits/"), say(f"600 ({DOCS}/v2/rate-limits)")]),
    ("web-01", [fetch(f"{DOCS}/changelog"), say(f"600 requests per minute ({DOCS}/changelog)")]),
    ("web-02", [fetch(f"{PRESS}/kessick-sea-trials-complete#top"),
                say(f"**2026-09-29**, {PRESS}/kessick-sea-trials-complete")]),
]


@pytest.mark.parametrize(("task_id", "script"), GROUNDED, ids=[f"{t[0]}-{i}" for i, t in enumerate(GROUNDED)])
async def test_the_right_page_reached_another_way_still_scores(factory, task_id, script):
    status, failed = await failed_checks(factory, task_id, *script, recipe="v2-web")
    assert status == "SUCCESS" and not failed, failed
