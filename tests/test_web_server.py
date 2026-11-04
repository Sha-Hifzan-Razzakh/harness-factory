"""The web server over recorded corpora (ADR 0018): versions and hashes, ranking, fetch bounds, redirects and 404s,
the read-only mount, the run snapshot, and that adding it moved no existing identity."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest
import yaml

from harness_factory import settings
from harness_factory.env import corpus as corpora
from harness_factory.env.mcp.impl import web_server, webindex
from harness_factory.env.sandbox import DockerSandbox, LocalSandbox
from harness_factory.mcp.pool import MCPSessionPool
from harness_factory.provision.provisioner import allowed_tools, reaches, task_class
from harness_factory.runner.runner import plan, run_one
from harness_factory.substrate.errors import HarnessError
from harness_factory.tasks.model import load_task

DOCS = "https://docs.relaymail.example"


# ------------------------------------------------------------------ a corpus of our own, in tmp_path

def page(url: str, title: str, text: str, fetched: str = "2026-10-01", published: str | None = None) -> str:
    head = {"url": url, "title": title, "fetched": fetched, **({"published": published} if published else {})}
    return f"---\n{yaml.safe_dump(head, sort_keys=False)}---\n{text}\n"


def make_catalog(root: Path, pages: dict[str, str], redirects=(), gone=(), freeze: bool = True) -> Path:
    """root/corpora/demo (a corpus) and root/tasks/demo-task (a task naming it); returns the task folder."""
    c = root / "corpora" / "demo"
    (c / "pages").mkdir(parents=True)
    (c / "corpus.yaml").write_text(yaml.safe_dump({
        "id": "demo", "title": "Demo", "description": "label, not identity",
        "redirects": [{"from": a, "to": b, "status": s} for a, b, s in redirects],
        "gone": [{"url": u, "status": s} for u, s in gone]}, sort_keys=False))
    for name, body in pages.items():
        (c / "pages" / name).parent.mkdir(parents=True, exist_ok=True)
        (c / "pages" / name).write_bytes(body.encode())
    if freeze:
        corpora.freeze(c)
    t = root / "tasks" / "demo-task"
    t.mkdir(parents=True)
    (t / "task.yaml").write_text(yaml.safe_dump({
        "id": "demo-task", "domain": "research", "instruction": "Look it up.", "env_seed": {"corpus": "demo"},
        "allowed_tools": ["web.search", "web.fetch"],
        "oracle": {"checks": [{"id": "a", "kind": "output_regex", "pattern": "x", "weight": 1}]}}))
    return t


LONG = "\n".join(f"Line {i:04d} of the long page, which talks about gizmos and doodads." for i in range(400))
DEMO = {
    "a.md": page("https://ex.example/a", "Widgets", "All about widgets. Widgets are small."),
    "b.md": page("https://ex.example/b", "Gadgets", "Gadgets, not widgets.", published="2026-01-05"),
    "long.md": page("https://ex.example/long", "The long page", LONG),
    "twin1.md": page("https://ex.example/twin-z", "Twin", "Sprockets."),
    "twin2.md": page("https://ex.example/twin-a", "Twin", "Sprockets."),
}
DEMO_REDIRECTS = [("https://ex.example/old-a", "https://ex.example/a", 301),
                  ("https://ex.example/older-a", "https://ex.example/old-a", 308),
                  ("https://ex.example/moved", "https://ex.example/removed", 302)]
DEMO_GONE = [("https://ex.example/removed", 404), ("https://ex.example/withdrawn", 410)]


@pytest.fixture
def demo(tmp_path):
    return load_task(make_catalog(tmp_path / "cat", DEMO, DEMO_REDIRECTS, DEMO_GONE))


@pytest.fixture
def demo_sb(demo):
    with LocalSandbox("web-test") as sb:
        sb.reset(demo)
        yield sb


def web(factory, sb) -> MCPSessionPool:
    return MCPSessionPool(factory.declared, ["web"], sb.env())


async def call(pool, tool, **args):
    r = await pool.call(f"web.{tool}", args)
    return r if r.is_error else json.loads(r.text)


# ------------------------------------------------------------------ versions and hashes

def test_the_version_is_the_content_address_of_what_is_served(tmp_path):
    t = make_catalog(tmp_path / "one", DEMO, DEMO_REDIRECTS, DEMO_GONE)
    base = load_task(t)
    v, h = base.env_seed.web_corpus(t).version, base.content_hash()
    assert len(v) == 16 and base.env_seed.web_corpus(t).stale() is None
    c = t.parent.parent / "corpora" / "demo"

    # Labels, file names and line endings are not identity.
    m = yaml.safe_load((c / "corpus.yaml").read_text())
    (c / "corpus.yaml").write_text(yaml.safe_dump({**m, "description": "another label"}))
    (c / "pages" / "a.md").rename(c / "pages" / "renamed.md")
    (c / "pages" / "b.md").write_bytes((c / "pages" / "b.md").read_bytes().replace(b"\n", b"\r\n"))
    assert load_task(t).env_seed.web_corpus(t).version == v and load_task(t).content_hash() == h

    # A page's text, a redirect or a 404 is: a new version, a new task hash, and the frozen manifest is stale.
    for change in (lambda: (c / "pages" / "renamed.md").write_text(DEMO["a.md"].replace("small", "tiny")),
                   lambda: (c / "corpus.yaml").write_text(yaml.safe_dump({**m, "gone": m["gone"][:1]}))):
        change()
        now = load_task(t)
        assert now.env_seed.web_corpus(t).version != v and now.content_hash() != h
        assert "changed since it was frozen" in now.env_seed.web_corpus(t).stale()


def test_a_task_without_a_corpus_keeps_its_hash(factory):
    task = factory.tasks.get("res-01")
    data = yaml.safe_load((task.source_dir / "task.yaml").read_text())
    data["env_seed"]["corpus"] = None
    from harness_factory.tasks.model import Task
    same = Task.model_validate(data)
    same.source_dir = task.source_dir
    assert same.content_hash() == task.content_hash()


def test_a_stale_manifest_is_refused_at_provisioning(tmp_path):
    task = load_task(make_catalog(tmp_path / "cat", DEMO, freeze=False))
    with LocalSandbox("web-stale") as sb, pytest.raises(HarnessError, match="no frozen version"):
        sb.reset(task)


@pytest.mark.parametrize(("pages", "redirects", "gone", "error"), [
    ({"a.md": page("http://Ex.example/a/", "A", "x")}, [], [], "write 'http://Ex.example/a/' as 'https://ex.example/a'"),
    ({"a.md": page("https://ex.example/a", "A", "x"), "b.md": page("https://ex.example/a", "B", "y")}, [], [],
     "listed 2 times"),
    (DEMO, [("https://ex.example/x", "https://ex.example/y", 301), ("https://ex.example/y", "https://ex.example/x", 301)],
     [], "redirect loop"),
    (DEMO, [("https://ex.example/x", "https://ex.example/nowhere", 301)], [], "neither a page nor"),
    (DEMO, [], [("https://ex.example/a", 404)], "listed 2 times"),
    ({"a.md": "no header\n"}, [], [], "YAML header"),
    ({"a.md": page("https://ex.example/a", "A", "x", fetched="Oct 2026")}, [], [], "YYYY-MM-DD"),
])
def test_a_malformed_corpus_is_refused(tmp_path, pages, redirects, gone, error):
    with pytest.raises(corpora.CorpusError, match=error.replace("(", r"\(").replace(".", r"\.")):
        make_catalog(tmp_path / "cat", pages, redirects, gone)


def test_pages_kept_in_dvc_say_how_to_get_them(tmp_path):
    c = tmp_path / "corpora" / "big"
    c.mkdir(parents=True)
    (c / "corpus.yaml").write_text("id: big\ntitle: Big\n")
    (c / "pages.dvc").write_text("outs:\n- path: pages\n")
    with pytest.raises(corpora.CorpusError, match="dvc pull"):
        corpora.load_corpus(c)


def test_the_catalog_corpora_are_frozen():
    roots = corpora.all_corpora()
    assert {r.name for r in roots} >= {"relaymail-docs", "saltmarsh-press"}
    for r in roots:
        assert corpora.load_corpus(r).stale() is None


def test_compiled_once_per_version_and_engine(demo, monkeypatch):
    c = demo.env_seed.web_corpus(demo.source_dir)
    first = corpora.compiled(c)
    assert first.is_relative_to(settings.CORPORA_DIR) and first.parent.name == c.index_id
    stamp = first.stat().st_mtime_ns
    assert corpora.compiled(c) == first and first.stat().st_mtime_ns == stamp      # reused, not rebuilt
    data = json.loads(first.read_text())
    assert data["version"] == c.version and data["index"]["engine"] == webindex.ENGINE_ID
    monkeypatch.setattr(webindex, "ENGINE_ID", "another-engine")                   # a new engine is a new index
    assert c.index_id != first.parent.name and corpora.compiled(c) != first


# ------------------------------------------------------------------ search

def test_ranking_is_deterministic_with_url_tie_breaks():
    pages = [{"url": u, "title": "Twin", "text": "Sprockets."} for u in ("https://x.example/zulu", "https://x.example/alfa")]
    pages.append({"url": "https://x.example/m", "title": "Other", "text": "sprockets sprockets sprockets"})
    ranked = webindex.rank(webindex.build_index(pages), "sprocket")
    assert [u for u, _ in ranked] == ["https://x.example/m", "https://x.example/alfa", "https://x.example/zulu"]
    assert ranked[1][1] == ranked[2][1]                                           # a real tie, broken by URL
    assert webindex.rank(webindex.build_index(list(reversed(pages))), "sprocket") == ranked
    assert webindex.tokens("The Rate Limits and retries") == ["rate", "limit", "retry"]


async def test_search_ranks_pages_and_pages_through_results(factory, demo_sb):
    async with web(factory, demo_sb) as demo_web:
        first = await call(demo_web, "search", query="widgets")
        assert first["total"] == 2 and first["pages"] == 1 and first["next_page"] is None
        assert [r["url"] for r in first["results"]] == ["https://ex.example/a", "https://ex.example/b"]
        assert first["results"][0] == {"rank": 1, "url": "https://ex.example/a", "title": "Widgets",
                                       "snippet": "All about widgets.", "fetched": "2026-10-01"}
        assert first["results"][1]["published"] == "2026-01-05"
        assert await call(demo_web, "search", query="widgets") == first               # same query, same answer
        twins = await call(demo_web, "search", query="sprockets")
        assert [r["url"] for r in twins["results"]] == ["https://ex.example/twin-a", "https://ex.example/twin-z"]
        assert (await call(demo_web, "search", query="nothing matches this"))["results"] == []
        for args, why in (({"query": "  "}, "empty"), ({"query": "the and of"}, "no searchable words"),
                          ({"query": "widgets", "page": 2}, "only 1 page"), ({"query": "widgets", "page": 0}, "1 to 5"),
                          ({"query": "x" * 501}, "longer than")):
            r = await call(demo_web, "search", **args)
            assert r.is_error and why in r.text, (args, r.text)


async def test_search_over_the_docs_corpus(factory):
    with LocalSandbox("web-docs") as sb:
        sb.reset(factory.tasks.get("web-01"))
        async with MCPSessionPool(factory.declared, ["web"], sb.env()) as pool:
            r = await call(pool, "search", query="rate limit")
            urls = [x["url"] for x in r["results"]]
            # the retired page ranks above the current one: the trap web-01 is built on
            assert urls[:2] == [f"{DOCS}/v1/rate-limits", f"{DOCS}/v2/rate-limits"]
            assert r["total"] > 5 and r["next_page"] == 2
            more = await call(pool, "search", query="rate limit", page=2)
            assert more["results"][0]["rank"] == 6 and not set(urls) & {x["url"] for x in more["results"]}


@pytest.mark.parametrize(("task_id", "url"), [("web-01", f"{DOCS}/v2/rate-limits"),
                                              ("web-02", ("https://press.saltmarsh-ferries.example/news/"
                                                          "kessick-sea-trials-complete"))])
def test_the_reference_search_finds_the_page_the_reference_fetches(factory, task_id, url):
    from harness_factory.tasks.reference import load_reference
    task = factory.tasks.get(task_id)
    query = next(s["args"]["query"] for s in load_reference(task).plan if s["tool"] == "web.search")
    index = task.env_seed.web_corpus(task.source_dir).compile()["index"]
    top = [u for u, _ in webindex.rank(index, query)[:web_server.RESULTS_PER_PAGE]]
    assert url in top


# ------------------------------------------------------------------ fetch

async def test_fetch_pages_redirects_and_errors(factory, demo_sb):
    async with web(factory, demo_sb) as demo_web:
        a = await call(demo_web, "fetch", url="http://EX.example/a/#intro")          # normalised like a browser
        assert a == {"url": "https://ex.example/a", "status": 200, "title": "Widgets", "fetched": "2026-10-01",
                     "offset": 0, "length": len("All about widgets. Widgets are small."),
                     "text": "All about widgets. Widgets are small.", "next_offset": None}
        hop = await call(demo_web, "fetch", url="https://ex.example/older-a")
        assert hop["url"] == "https://ex.example/a" and hop["redirected_from"] == [
            {"url": "https://ex.example/older-a", "status": 308}, {"url": "https://ex.example/old-a", "status": 301}]
        for url, why in (("https://ex.example/removed", "HTTP 404 Not Found: https://ex.example/removed"),
                         ("https://ex.example/moved", ("HTTP 404 Not Found: https://ex.example/removed (redirected "
                                                       "from https://ex.example/moved)")),
                         ("https://ex.example/withdrawn", "HTTP 410 Gone"),
                         ("https://ex.example/never-was", "HTTP 404 Not Found"),
                         ("https://elsewhere.example/a", "could not connect to elsewhere.example"),
                         ("ftp://ex.example/a", "not an http(s) URL"), ("", "not an http(s) URL")):
            r = await call(demo_web, "fetch", url=url)
            assert r.is_error and why in r.text, (url, r.text)


async def test_a_long_page_is_read_in_bounded_parts(factory, demo_sb):
    async with web(factory, demo_sb) as demo_web:
        parts, offset = [], 0
        while offset is not None:
            r = await call(demo_web, "fetch", url="https://ex.example/long", offset=offset)
            assert len(r["text"]) <= web_server.PAGE_CHARS and r["length"] == len(LONG)
            assert r["offset"] == offset and (r["next_offset"] is None or r["text"].endswith("\n"))   # whole lines
            parts.append(r["text"])
            offset = r["next_offset"]
        assert "".join(parts) == LONG and len(parts) > len(LONG) // web_server.PAGE_CHARS
        for bad in (-1, len(LONG)):
            r = await call(demo_web, "fetch", url="https://ex.example/long", offset=bad)
            assert r.is_error and "offset" in r.text


async def test_too_many_redirects(tmp_path, factory):
    hops = [(f"https://ex.example/r{i}", f"https://ex.example/r{i + 1}", 301) for i in range(7)]
    task = load_task(make_catalog(tmp_path / "cat", DEMO, [*hops, ("https://ex.example/r7", "https://ex.example/a",
                                                                   301)]))
    with LocalSandbox("web-hops") as sb:
        sb.reset(task)
        async with MCPSessionPool(factory.declared, ["web"], sb.env()) as pool:
            assert "too many redirects" in (await call(pool, "fetch", url="https://ex.example/r0")).text
            assert (await call(pool, "fetch", url="https://ex.example/r3"))["url"] == "https://ex.example/a"


async def test_a_task_without_a_corpus_has_no_web(factory):
    with LocalSandbox("web-none") as sb:
        sb.reset(factory.tasks.get("fs-01"))
        assert not list(sb.web_dir.iterdir()) and sb.corpus_info is None
        async with MCPSessionPool(factory.declared, ["web"], sb.env()) as pool:
            r = await call(pool, "search", query="anything")
            assert r.is_error and "no web" in r.text


# ------------------------------------------------------------------ where the corpus is, and that it can't change

def test_the_corpus_is_mounted_read_only_outside_the_workspace(demo, factory):
    with LocalSandbox("web-mount") as sb:
        sb.reset(demo)
        mounted = sb.web_dir / web_server.CORPUS_FILE
        assert sb.env()["HF_WEB"] == str(sb.web_dir) and not sb.web_dir.is_relative_to(sb.workspace)
        assert json.loads(mounted.read_text())["version"] == demo.env_seed.web_corpus(demo.source_dir).version
        assert not list(sb.workspace.rglob("*"))                                 # nothing in the workspace
        if os.name == "posix":
            assert stat.S_IMODE(mounted.stat().st_mode) == 0o444
            assert stat.S_IMODE(sb.web_dir.stat().st_mode) == 0o555
            if os.geteuid() != 0:                                                # root ignores file modes
                with pytest.raises(PermissionError):
                    mounted.write_text("{}")
        before = sb.web_dir.stat().st_ino
        sb.reset(factory.tasks.get("fs-01"))          # same folder (a Docker bind mount stays valid), now empty
        assert sb.web_dir.stat().st_ino == before and not list(sb.web_dir.iterdir()) and sb.corpus_info is None
        sb.reset(demo)
    assert not sb.root.exists()                       # teardown removes the read-only files too


def test_docker_mounts_the_corpus_read_only(tmp_path):
    sb = DockerSandbox.__new__(DockerSandbox)
    sb.label, sb.image, sb.container = "x", "img", "hf-x"
    sb.workspace, sb.db_path = tmp_path / "workspace", tmp_path / "state" / "task.sqlite"
    assert f"{tmp_path / 'state' / 'web'}:/state/web:ro" in sb.run_args()
    assert sb.env()["HF_WEB"] == "/state/web"


# ------------------------------------------------------------------ who gets it, identity, runs

def test_only_recipes_opening_web_reach_the_research_tasks(factory):
    d, web, base = factory.declared, factory.recipes["v2-web"], factory.recipes["v2-base"]
    web1 = factory.tasks.get("web-01")
    assert reaches(web, web1, d) and not reaches(base, web1, d)
    assert {"web.search", "web.fetch", "fs.read_file"} == allowed_tools(web, web1, d)
    assert not {r for r in allowed_tools(web, factory.tasks.get("res-01"), d) if r.startswith("web.")}
    assert task_class(web1, d) == "UNKNOWN_PATH|READ|R0"
    pairs = {(s.task_id, s.recipe_id) for s in plan(factory, ["v2-base", "v2-web"], "scripted", ["scripted/replay"],
                                                    task_ids=["fs-01", "web-01"])}
    assert pairs == {("fs-01", "v2-base"), ("fs-01", "v2-web"), ("web-01", "v2-web")}


def test_adding_web_moved_no_existing_identity(factory, monkeypatch):
    d = factory.declared
    base = factory.recipes["v2-base"].tools.mcp_servers
    assert d.env_hash(base, "openai/gpt-oss-120b") == "a55c2fb739f1"
    assert factory.recipes["v2-base"].content_hash() == "79abd03818943f65"
    web = d.env_hash(factory.recipes["v2-web"].tools.mcp_servers, "openai/gpt-oss-120b")
    monkeypatch.setattr(webindex, "ENGINE_ID", "another-engine")        # the ranking engine is the web's environment
    assert d.env_hash(factory.recipes["v2-web"].tools.mcp_servers, "openai/gpt-oss-120b") != web
    assert d.env_hash(base, "openai/gpt-oss-120b") == "a55c2fb739f1"


@pytest.mark.parametrize("task_id", ["web-01", "web-02"])
async def test_research_runs_succeed_and_record_the_corpus(factory, task_id):
    task = factory.tasks.get(task_id)
    rec = await run_one(factory, task, factory.recipes["v2-web"], "scripted/replay", "scripted")
    assert rec.status.value == "SUCCESS" and rec.composite == pytest.approx(1.0), rec.grades
    snap = next(g for g in rec.grades if g["grader_id"] == "state_diff")["snapshot"]
    c = task.env_seed.web_corpus(task.source_dir)
    assert snap["web_corpus"] == c.info() and snap["web_corpus"]["version"] == c.version
    assert snap["web_corpus"]["index_id"] == c.index_id and snap["web_corpus"]["engine"] == webindex.ENGINE_ID
