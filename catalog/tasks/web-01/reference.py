"""Reference solution: the minimal tool path (ADR 0018). The rate-limit search ranks the retired v1 page first."""

PLAN = [{"tool": "fs.read_file", "args": {"path": "brief.md"}},
        {"tool": "web.search", "args": {"query": "Relaymail API rate limit per key requests per minute"}},
        {"tool": "web.fetch", "args": {"url": "https://docs.relaymail.example/v2/rate-limits"}}]

FINAL = "600 requests per minute per API key (https://docs.relaymail.example/v2/rate-limits)"
