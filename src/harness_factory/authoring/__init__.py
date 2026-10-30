"""Authoring (ADR 0009): the one place that writes catalog items, behind the CLI, the API and the UI.

Every change is staged in a temporary copy of the catalog and checked with the factory's own loaders and
validators in a separate process (`snapshot`), so a write can never leave the catalog in a state `hfx validate`
would reject. The diff between the current and staged snapshots says which recipe hashes and tool environments
change; a change to an existing identity needs confirmation before it is written.

Files stay the source of truth and git stays the history: authoring writes files, it never commits.
"""
