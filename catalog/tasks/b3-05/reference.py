"""Reference solution: the EU file already has its own retry list, which replaces the default (lists are not merged),
so 14 is appended to the EU list; max_attempts must equal the number of retries, so it becomes 4."""

PLAN = [{"tool": "fs.read_file", "args": {"path": "docs/dunning.md"}},
        {"tool": "fs.list_dir", "args": {"path": "config/dunning"}},
        {"tool": "fs.read_file", "args": {"path": "config/dunning/eu.toml"}},
        {"tool": "fs.write_file",
         "args": {"path": "config/dunning/eu.toml",
                  "content": "# EU: SEPA direct debits settle slower, so retries are spaced further apart.\n"
                             "[retry]\n"
                             "retry_days = [2, 5, 10, 14]\n"
                             "max_attempts = 4\n"
                             "\n"
                             "[final]\n"
                             "after_days = 30\n"}}]

FINAL = "EU now retries on days 2, 5, 10 and 14 (max_attempts = 4). Defaults and other regions are unchanged."
