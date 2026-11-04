"""Reference solution: the minimal tool path (ADR 0018). The search shows each release's published date; the
latest one from the operator moves the date again."""

PLAN = [{"tool": "fs.read_file", "args": {"path": "brief.md"}},
        {"tool": "web.search", "args": {"query": "Kessick first passenger sailing date"}},
        {"tool": "web.fetch", "args": {"url": "https://press.saltmarsh-ferries.example/news/kessick-sea-trials-complete"}}]

FINAL = "2026-09-29 (https://press.saltmarsh-ferries.example/news/kessick-sea-trials-complete)"
