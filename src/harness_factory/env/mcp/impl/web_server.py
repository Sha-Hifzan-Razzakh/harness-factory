"""web MCP server: search the web and fetch pages, over a recorded, versioned corpus (ADR 0018). No network, ever.

The corpus is a snapshot of a few sites (catalog/corpora/<id>/), compiled by the factory once per version (pages,
redirects, recorded 404s and a BM25 index; env/corpus.py) and mounted read-only into the run's state folder
(HF_WEB, never the workspace). This server only reads that file. A task names its corpus with
`env_seed.corpus`; a task without one has no web, and every call says so.

- search(query, page): ranked results (url, title, snippet, dates), 5 per page, at most 5 pages. The ranking is
  webindex.rank: deterministic, ties in URL order.
- fetch(url, offset): the page's text, at most PAGE_CHARS characters per call (well under the tool executor's
  inline cap), with `next_offset` to read on, so a long page is still usable whole. Redirects are followed as a
  browser does, and reported; a recorded 404 / 410, or a path the site never had, is an HTTP error; a host the
  snapshot doesn't hold can't be reached.

Standard library only (plus its sibling webindex), so it runs inside the sandbox image.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError  # message reaches the model; ValueError would be masked

from harness_factory.env.mcp.impl.webindex import ENGINE_ID, normalize_url, rank, snippet, tokens

app = MCPServer("web", instructions="Search the web and fetch pages. This is a recorded snapshot of a few sites, "
                                    "not the live web: each page shows the date it was fetched, and pages can be "
                                    "out of date or contradict each other, so check dates and sources.")

CORPUS_FILE = "corpus.json"     # what the factory mounts in HF_WEB (env/corpus.py writes it)
RESULTS_PER_PAGE = 5
MAX_PAGES = 5
PAGE_CHARS = 6000
MAX_REDIRECTS = 5
MAX_QUERY_CHARS = 500
REASONS = {404: "Not Found", 410: "Gone"}

_loaded: dict | None = None     # this server process serves one run, so one corpus


def _corpus() -> dict:
    global _loaded
    if _loaded is None:
        where = os.environ.get("HF_WEB")
        path = Path(where) / CORPUS_FILE if where else None
        if path is None or not path.is_file():
            raise ToolError("there is no web to search in this task (no recorded corpus is mounted)")
        with open(path, encoding="utf-8") as fh:            # read-only, like the mount
            data = json.load(fh)
        if data.get("index", {}).get("engine") != ENGINE_ID:
            raise ToolError("the mounted corpus was indexed by another search engine version")
        _loaded = data
    return _loaded


def _hosts(c: dict) -> set[str]:
    return {urlsplit(u).hostname or "" for part in ("pages", "redirects", "gone") for u in c[part]}


@app.tool()
def search(query: str, page: int = 1) -> dict:
    """Search the web. Returns {query, page, pages, total, results, next_page}: 5 results per page, best first,
    each with url, title, a snippet, the date the page was fetched and, when the page states one, the date it was
    published. Ask for more with page=2 and so on (at most 5 pages). Open a result with fetch(url)."""
    c = _corpus()
    if not isinstance(query, str) or not query.strip():
        raise ToolError("query is empty")
    if len(query) > MAX_QUERY_CHARS:
        raise ToolError(f"query is longer than {MAX_QUERY_CHARS} characters")
    if not tokens(query):
        raise ToolError(f"query {query!r} has no searchable words")
    if isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= MAX_PAGES:
        raise ToolError(f"page is a whole number from 1 to {MAX_PAGES}")
    hits = rank(c["index"], query)
    reachable = hits[:RESULTS_PER_PAGE * MAX_PAGES]
    pages = -(-len(reachable) // RESULTS_PER_PAGE)
    if hits and page > pages:
        raise ToolError(f"there {'is' if pages == 1 else 'are'} only {pages} page{'s' * (pages != 1)} of results")
    start = (page - 1) * RESULTS_PER_PAGE
    results = []
    for n, (url, _score) in enumerate(reachable[start:start + RESULTS_PER_PAGE], start + 1):
        p = c["pages"][url]
        r = {"rank": n, "url": url, "title": p["title"], "snippet": snippet(p["text"], query),
             "fetched": p["fetched"]}
        if p.get("published"):
            r["published"] = p["published"]
        results.append(r)
    return {"query": query, "page": page, "pages": pages, "total": len(hits), "results": results,
            "next_page": page + 1 if page < pages else None}


@app.tool()
def fetch(url: str, offset: int = 0) -> dict:
    """Fetch a web page as text (markdown). Follows redirects. Returns {url, status, title, fetched, published?,
    redirected_from?, offset, length, text, next_offset}: at most 6000 characters of text per call; when
    next_offset is set, call fetch again with offset=next_offset to read on."""
    c = _corpus()
    want = normalize_url(url) if isinstance(url, str) else None
    if want is None:
        raise ToolError(f"not an http(s) URL: {url!r}")
    hops: list[dict] = []
    at = want
    while at in c["redirects"]:
        if len(hops) == MAX_REDIRECTS:
            raise ToolError(f"too many redirects fetching {want}")
        r = c["redirects"][at]
        hops.append({"url": at, "status": r["status"]})
        at = r["to"]
    via = f" (redirected from {want})" if hops else ""
    if at in c["gone"]:
        status = c["gone"][at]
        raise ToolError(f"HTTP {status} {REASONS.get(status, 'Error')}: {at}{via}")
    page = c["pages"].get(at)
    if page is None:
        host = urlsplit(at).hostname or ""
        if host not in _hosts(c):
            raise ToolError(f"could not connect to {host}: this browser has a recorded snapshot of "
                            f"{', '.join(sorted(_hosts(c)))} only")
        raise ToolError(f"HTTP 404 Not Found: {at}{via}")
    text = page["text"]
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0 or (offset and offset >= len(text)):
        raise ToolError(f"offset is a whole number from 0 to {max(len(text) - 1, 0)} (the page has {len(text)} "
                        f"characters)")
    end = min(offset + PAGE_CHARS, len(text))
    if end < len(text):                                 # end on a paragraph or line break when there is one
        cut = text.rfind("\n", offset + PAGE_CHARS // 2, end)
        end = cut + 1 if cut != -1 else end
    out = {"url": at, "status": 200, "title": page["title"], "fetched": page["fetched"]}
    if page.get("published"):
        out["published"] = page["published"]
    if hops:
        out["redirected_from"] = hops
    out.update({"offset": offset, "length": len(text), "text": text[offset:end],
                "next_offset": end if end < len(text) else None})
    return out


if __name__ == "__main__":
    app.run()
