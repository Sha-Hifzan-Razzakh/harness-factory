"""The web server's search engine (ADR 0018): BM25 over a recorded corpus, standard library only.

One module for both sides, so a query is tokenised exactly as the pages were:
- the factory builds the index once per corpus version (env/corpus.py) and caches it with the pages;
- the web server (web_server.py), inside the sandbox, loads that index and ranks queries against it.

Deterministic by construction: no clock, no randomness, no hash-order dependence. Scores are rounded before
sorting and ties go to the URL in order, so the same query on the same corpus version always returns the same
page of results on every machine. Changing anything here that changes a ranking or a snippet is a new engine:
bump ENGINE's version. ENGINE_ID is part of `env_hash` for recipes that open the web server and of every
index id.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

ENGINE: dict[str, Any] = {
    "name": "web-bm25",
    "version": 1,
    "k1": 1.2,
    "b": 0.75,
    "title_boost": 2,        # title tokens count this many times; URL path tokens once; body once
    "tokenizer": "lowercase ascii alnum runs, stopwords dropped, plural s stripped",
    "snippet_chars": 240,
    "score_digits": 9,
}
ENGINE_ID = hashlib.sha256(json.dumps(ENGINE, sort_keys=True).encode()).hexdigest()[:12]

_WORD = re.compile(r"[a-z0-9]+")
_STOP = ("a an and are as at be but by can do does for from has have how i if in into is it its of on or our so that "
         "the their them then there these this to was we were what when where which who why will with you your")
STOPWORDS = frozenset(_STOP.split(" "))
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_MARKUP = re.compile(r"[#*`>|_]+|\[([^\]]*)\]\([^)]*\)")


def normalize_url(url: str) -> str | None:
    """The one spelling of a URL the corpus is keyed by, or None if it isn't an http(s) URL with a host.

    http and https are the same page, no scheme means https, the host is case-insensitive, a #fragment is never
    sent to a server, and a trailing slash is dropped (except the site root, "/"). The query string is kept."""
    from urllib.parse import urlsplit, urlunsplit
    raw = (url or "").strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = "https://" + raw.lstrip("/")
    try:
        u = urlsplit(raw)
        port = u.port
    except ValueError:
        return None
    if u.scheme.lower() not in ("http", "https") or not u.hostname:
        return None
    host = u.hostname.lower() + (f":{port}" if port not in (None, 80, 443) else "")
    path = u.path or "/"
    if len(path) > 1:
        path = path.rstrip("/") or "/"
    return urlunsplit(("https", host, path, u.query, ""))


def stem(word: str) -> str:
    """Plural s only, so "limits" finds "limit" and "retries" finds "retry". Nothing smarter, on purpose."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def tokens(text: str) -> list[str]:
    return [stem(w) for w in _WORD.findall(text.lower()) if w not in STOPWORDS]


def _url_words(url: str) -> str:
    return re.sub(r"^[a-z]+://", "", url).replace("/", " ").replace("-", " ").replace(".", " ")


def doc_tokens(page: dict) -> list[str]:
    return (tokens(page.get("title", "")) * ENGINE["title_boost"] + tokens(_url_words(page["url"]))
            + tokens(page.get("text", "")))


def build_index(pages: list[dict]) -> dict:
    """Term frequencies per page and document frequencies, for pages given in URL order."""
    docs, df = [], {}
    for p in pages:
        tf: dict[str, int] = {}
        toks = doc_tokens(p)
        for t in toks:
            tf[t] = tf.get(t, 0) + 1
        for t in tf:
            df[t] = df.get(t, 0) + 1
        docs.append({"url": p["url"], "length": len(toks), "tf": dict(sorted(tf.items()))})
    n = len(docs)
    avgdl = sum(d["length"] for d in docs) / n if n else 0.0
    return {"engine": ENGINE_ID, "n": n, "avgdl": avgdl, "df": dict(sorted(df.items())), "docs": docs}


def rank(index: dict, query: str) -> list[tuple[str, float]]:
    """(url, score) for every page that shares a term with the query, best first; ties in URL order."""
    terms = sorted(set(tokens(query)))
    n, avgdl, df = index["n"], index["avgdl"] or 1.0, index["df"]
    k1, b = ENGINE["k1"], ENGINE["b"]
    out = []
    for d in index["docs"]:
        score = 0.0
        for t in terms:
            f = d["tf"].get(t, 0)
            if not f:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            score += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * d["length"] / avgdl))
        if score > 0:
            out.append((d["url"], round(score, ENGINE["score_digits"])))
    out.sort(key=lambda x: (-x[1], x[0]))
    return out


def plain(text: str) -> str:
    """Markdown as plain text, for snippets."""
    return _MARKUP.sub(lambda m: m.group(1) or "", text)


def _sentences(text: str) -> list[str]:
    """Body sentences in page order: headings skipped, list items and table rows one each, other lines of a
    paragraph joined (a sentence often wraps)."""
    out = []
    for para in re.split(r"\n\s*\n", text):
        lines = [ln.strip() for ln in para.splitlines() if ln.strip()]
        if not lines or lines[0].startswith("#"):
            continue
        if all(ln.startswith(("- ", "* ", "|")) or re.match(r"^\d+\. ", ln) for ln in lines):
            units = lines
        else:
            units = [" ".join(lines)]
        for u in units:
            out += [x.strip() for x in _SENTENCE.split(plain(u)) if x.strip(" -|")]
    return out


def snippet(text: str, query: str) -> str:
    """The sentence that covers the most distinct query terms (the earliest on a tie), cut to snippet_chars."""
    want = set(tokens(query))
    best, best_hits = "", -1
    for s in _sentences(text):
        hits = len(want & set(tokens(s)))
        if hits > best_hits:
            best, best_hits = s, hits
    best = " ".join(best.strip(" -|").split())
    limit = ENGINE["snippet_chars"]
    return best if len(best) <= limit else best[:limit - 1].rstrip() + "…"
