"""Versioning for the authoring service (mixed into AuthoringService): every item's files per version, history,
diffs, restore, archive and purge, tags, an activity feed, changes made outside authoring (sync), models as an
editable kind, tasks as a read-only one, and results per version.

An item's *parts* are the text that makes it up:
    prompt, skill, recipe, fault   {<path>: file text}
    server                         {"catalog/tools.yaml#<id>": its tools.yaml entry, <module path>: code (local)}
    model                          {"catalog/providers.yaml#<provider>/<model>": its providers.yaml entry}
    task (read only)               {"<task dir>/task.yaml": ..., "<task dir>/reference.py": ...}
"""

from __future__ import annotations

import statistics
from pathlib import Path
from typing import Any

import yaml

from harness_factory.authoring import fsops, providers_edit, toolcode
from harness_factory.authoring.fsops import FileOp
from harness_factory.authoring.models import KINDS, READ_ONLY_KINDS, ChangeResult, Issue, Item
from harness_factory.authoring.versions import Entry, diff, summary

TOOLS = "catalog/tools.yaml"
PROVIDERS = "catalog/providers.yaml"
HASH_KEY = {"prompt": ("prompts", "hash"), "skill": ("skills", "hash"), "recipe": ("recipes", "hash"),
            "server": ("servers", "env"), "fault": ("faults", "hash"), "model": ("models", "hash"),
            "task": ("tasks", "hash")}


class HistoryMixin:
    # Provided by AuthoringService: root, journal, author, results_url, lock_path, snapshot(), _items(), used_by(),
    # _stage_and_apply(), _fault_path(), _tools_op(), _kind().

    # ---- items of the extra kinds ------------------------------------------------------------------------------

    def _extra_items(self, snap: dict, kind: str, errors: dict) -> list[Item]:
        out = []
        if kind == "model":
            for mid, v in snap.get("models", {}).items():
                out.append(Item(kind="model", id=mid, path=PROVIDERS, hash=v["hash"], summary=v["summary"],
                                detail={"provider": v["provider"], "canonical": v["canonical"],
                                        "provider_model_id": v["provider_model_id"], "provider_kind": v["kind"]},
                                issues=errors.get(("providers", "providers.yaml"), [])))
        elif kind == "task":
            for tid, v in snap.get("tasks", {}).items():
                out.append(Item(kind="task", id=tid, path=v.get("path"), hash=v.get("hash"),
                                summary=f"{v.get('split')} · {v.get('instruction', '')}", builtin=True,
                                detail={"split": v.get("split"), "allowed_tools": v.get("allowed_tools"),
                                        "key": v.get("key")}, issues=errors.get(("task", tid), [])))
        return out

    @staticmethod
    def _exists(snap: dict, kind: str, item_id: str) -> bool:
        section, _ = HASH_KEY[kind]
        return item_id in snap.get(section, {})

    @staticmethod
    def _hash(snap: dict, kind: str, item_id: str) -> str | None:
        section, key = HASH_KEY[kind]
        return (snap.get(section, {}).get(item_id) or {}).get(key)

    # ---- parts ---------------------------------------------------------------------------------------------

    def _read(self, path: str, root: Path, ops: list[FileOp] | None) -> str | None:
        for op in reversed(ops or []):
            if op.path == path:
                return op.text
        for base in (root, self.root):
            p = base / path
            if p.is_file():
                return p.read_text(encoding="utf-8")
            if base == root and not path.startswith("src/"):
                return None                     # the staged copy says it's gone; don't fall back to the repo
        return None

    def parts(self, kind: str, item_id: str, snap: dict, root: Path | None = None,
              ops: list[FileOp] | None = None) -> dict[str, str]:
        root = root or self.root
        out: dict[str, str] = {}
        if kind == "server":
            text = self._read(TOOLS, root, ops)
            doc = yaml.safe_load(text) if text else {"servers": []}
            entry = next((s for s in doc.get("servers", []) if s["id"] == item_id), None)
            if entry is not None:
                out[f"{TOOLS}#{item_id}"] = yaml.safe_dump(entry, sort_keys=False, width=120)
                if entry.get("transport", "stdio") == "stdio":
                    code = self._read(toolcode.module_path(item_id), root, ops)
                    if code is not None:
                        out[toolcode.module_path(item_id)] = code
            return out
        if kind == "model":
            text = self._read(PROVIDERS, root, ops) or ""
            provider, _, canonical = item_id.partition("/")
            entry = providers_edit.entry_text(text, provider, canonical)
            return {f"{PROVIDERS}#{item_id}": entry} if entry is not None else {}
        paths = self._paths(kind, item_id, snap, ops)
        for path in paths:
            text = self._read(path, root, ops)
            if text is not None:
                out[path] = text
        return out

    def _paths(self, kind: str, item_id: str, snap: dict, ops: list[FileOp] | None) -> list[str]:
        if kind == "prompt":
            return [f"prompts/{item_id}.j2"]
        if kind == "task":
            path = (snap.get("tasks", {}).get(item_id) or {}).get("path")
            return [f"{path}/task.yaml", f"{path}/reference.py"] if path else []
        section = {"skill": "skills", "recipe": "recipes"}.get(kind)
        path = (snap.get(section, {}).get(item_id) or {}).get("path") if section else None
        if path is None and kind == "fault":
            path = self._fault_path(item_id)
        if path is None:                              # a new item: the file its ops write
            prefix = {"skill": f"catalog/skills/{item_id}/", "recipe": "catalog/recipes/"}.get(kind, "")
            path = next((op.path for op in ops or [] if op.text is not None and op.path.startswith(prefix)), None)
        return [path] if path else []

    @staticmethod
    def _diff_text(before: dict[str, str], after: dict[str, str]) -> str:
        return diff(before, after, "current", "proposed")

    # ---- recording -----------------------------------------------------------------------------------------

    def _dependents(self, snap: dict, kind: str, item_id: str) -> dict[str, str]:
        """Recipes whose identity includes this item, with their hash in `snap`: what 'this version' ran as."""
        recipes = snap.get("recipes", {})
        if kind == "recipe":
            ids = {item_id} | {r for r, v in recipes.items() if v.get("base") == item_id or v.get("delegate") == item_id}
        elif kind == "prompt":
            ids = {r for r, v in recipes.items() if item_id in v["prompt_refs"]}
        elif kind == "skill":
            ids = {r for r, v in recipes.items() if item_id in v["skill_ids"]}
        elif kind == "server":
            ids = {r for r, v in recipes.items() if item_id in v["servers"]}
        else:
            ids = set()
        return {r: recipes[r]["hash"] for r in sorted(ids) if r in recipes}

    def _baseline(self, snap: dict, kind: str, item_id: str, author: str, source: str = "import",
                  note: str = "state when history began") -> Entry:
        return self.journal.append(
            author=author, kind=kind, id=item_id, action="baseline", version=1,
            hash_after=self._hash(snap, kind, item_id), parts_after=self.journal.put_parts(self.parts(kind, item_id, snap)),
            note=note, source=source, recipe_hashes=self._dependents(snap, kind, item_id))

    def _record_baselines(self, cur: dict, result: ChangeResult) -> None:
        targets = [(result.kind, result.id)] + [("recipe", r) for r in result.impact.recipes_changed]
        for kind, item_id in targets:
            if self._exists(cur, kind, item_id) and self.journal.latest(kind, item_id) is None:
                self._baseline(cur, kind, item_id, self.author)

    def _record_change(self, result: ChangeResult, cur: dict, new: dict, before: dict, after: dict,
                       note: str, author: str | None) -> None:
        author = author or self.author
        action = result.action if result.action in ("archive", "restore") else (
            "create" if not before else "update")
        imp = result.impact.model_dump()
        e = self.journal.append(
            author=author, kind=result.kind, id=result.id, action=action,
            version=self.journal.next_version(result.kind, result.id),
            hash_before=self._hash(cur, result.kind, result.id), hash_after=self._hash(new, result.kind, result.id),
            parts_before=self.journal.put_parts(before), parts_after=self.journal.put_parts(after), note=note,
            recipe_hashes=self._dependents(new, result.kind, result.id), impact=imp)
        result.version = e.version
        cause = f"{result.kind} {result.id} v{e.version}"
        for rid in result.impact.recipes_changed:
            if result.kind == "recipe" and rid == result.id:
                continue
            last = self.journal.latest("recipe", rid)
            self.journal.append(
                author=author, kind="recipe", id=rid, action="dependency",
                version=self.journal.next_version("recipe", rid), hash_before=self._hash(cur, "recipe", rid),
                hash_after=self._hash(new, "recipe", rid), parts_before=last.parts_after if last else {},
                parts_after=last.parts_after if last else {}, note=note, cause=cause,
                recipe_hashes={rid: self._hash(new, "recipe", rid) or ""})

    # ---- reading history -----------------------------------------------------------------------------------

    def history(self, kind: str, item_id: str) -> dict[str, Any]:
        kind = self._kind(kind, read_only=True)
        tags = self.journal.tags(kind, item_id)
        versions = [{**summary(e), "tags": tags.get(e.version, [])} for e in self.journal.versions(kind, item_id)]
        snap = self.snapshot()
        live = self._exists(snap, kind, item_id)
        drift = None
        if live and versions:
            cur = {k: self.journal.put(v) for k, v in self.parts(kind, item_id, snap).items()}
            last = self.journal.latest(kind, item_id)
            drift = cur != last.parts_after
        return {"kind": kind, "id": item_id, "live": live, "current_hash": self._hash(snap, kind, item_id),
                "versions": list(reversed(versions)), "changed_outside": bool(drift),
                "archived": not live and bool(versions) and versions[-1]["action"] == "archive"}

    def version_parts(self, kind: str, item_id: str, version: int | str) -> dict[str, str]:
        """A version's files; 'current' = what is in the catalog now."""
        kind = self._kind(kind, read_only=True)
        if version == "current":
            snap = self.snapshot()
            return self.parts(kind, item_id, snap) if self._exists(snap, kind, item_id) else {}
        return self.journal.get_parts(self.journal.version(kind, item_id, int(version)).parts_after)

    def diff_versions(self, kind: str, item_id: str, a: int | str, b: int | str = "current") -> str:
        return diff(self.version_parts(kind, item_id, a), self.version_parts(kind, item_id, b), f"v{a}", f"v{b}")

    def activity(self, limit: int = 100, kind: str | None = None, item_id: str | None = None) -> list[dict]:
        entries = self.journal.entries(kind, item_id)
        return [summary(e) for e in reversed(entries)][:limit]

    def archived_items(self) -> list[dict]:
        return [summary(e) for e in self.journal.archived()]

    # ---- changing history ----------------------------------------------------------------------------------

    def tag(self, kind: str, item_id: str, version: int, tags: list[str], author: str | None = None) -> dict:
        kind = self._kind(kind, read_only=True)
        with fsops.catalog_lock(self.lock_path):
            self.journal.version(kind, item_id, version)           # KeyError if there is no such version
            e = self.journal.append(author=author or self.author, kind=kind, id=item_id, action="tag",
                                    version=version, tags=[t.strip() for t in tags if t.strip()])
        return summary(e)

    def restore(self, kind: str, item_id: str, version: int, note: str = "", dry_run: bool = False,
                confirm: bool = False, author: str | None = None) -> ChangeResult:
        """Bring back a version's files as a new version, through the same checks as any change."""
        from harness_factory.authoring.service import BUILTIN_SERVERS, AuthoringError
        kind = self._kind(kind)
        try:
            entry = self.journal.version(kind, item_id, version)
        except KeyError as e:
            raise AuthoringError(str(e).strip("'\"")) from None
        result = ChangeResult(kind=kind, id=item_id, action="restore", status="rejected")
        if not entry.parts_after:
            result.issues = [Issue(where=f"{kind} {item_id}", detail=f"v{version} is the archived state (no files);"
                                                                     f" restore the version before it")]
            return result
        if kind == "server" and item_id in BUILTIN_SERVERS:
            result.issues = [Issue(where=f"server {item_id}", detail="a built-in server; edit it in the repo")]
            return result
        target = self.journal.get_parts(entry.parts_after)
        with fsops.catalog_lock(self.lock_path):
            snap = self.snapshot()
            current = self.parts(kind, item_id, snap) if self._exists(snap, kind, item_id) else {}
            ops = self._parts_ops(target, current)
            if not ops:
                result.status = "dry_run" if dry_run else "applied"
                result.notes = [f"the catalog already holds v{version}"]
                return result
            return self._stage_and_apply(result, snap, ops, dry_run, confirm,
                                         note=f"restored v{version}" + (f": {note}" if note else ""), author=author)

    def update_parts(self, kind: str, item_id: str, parts: dict[str, str], note: str = "", dry_run: bool = False,
                     confirm: bool = False, author: str | None = None) -> ChangeResult:
        """Edit an item's own files as text (what the editor shows), checked like any change. The parts must be the
        item's current parts (no new paths). Tool servers are edited through `update` instead: their code is
        checked, started and its tools discovered there."""
        from harness_factory.authoring.service import AuthoringError
        kind = self._kind(kind)
        if kind == "server":
            raise AuthoringError("edit a tool server through its form: its code is checked and its tools discovered")
        result = ChangeResult(kind=kind, id=item_id, action="update", status="rejected")
        with fsops.catalog_lock(self.lock_path):
            snap = self.snapshot()
            if not self._exists(snap, kind, item_id):
                raise AuthoringError(f"no {kind} {item_id!r}")
            current = self.parts(kind, item_id, snap)
            unknown = sorted(set(parts) - set(current))
            if unknown:
                result.issues = [Issue(where=f"{kind} {item_id}", detail=f"not a file of this item: {', '.join(unknown)}")]
                return result
            target = {**current, **{k: (v if v.endswith("\n") else v + "\n") for k, v in parts.items()}}
            ops = self._parts_ops(target, current)
            if not ops:
                result.status = "dry_run" if dry_run else "applied"
                result.notes = ["no change"]
                return result
            return self._stage_and_apply(result, snap, ops, dry_run, confirm, note=note, author=author)

    def _parts_ops(self, target: dict[str, str], current: dict[str, str]) -> list[FileOp]:
        ops: list[FileOp] = []
        for key in sorted(set(target) | set(current)):
            text = target.get(key)
            if text == current.get(key):
                continue
            if "#" in key:
                file, ident = key.split("#", 1)
                if file == TOOLS:
                    ops.append(self._tools_op(ident, yaml.safe_load(text) if text is not None else None))
                elif file == PROVIDERS:
                    provider, _, canonical = ident.partition("/")
                    doc = (self.root / PROVIDERS).read_text(encoding="utf-8")
                    ops.append(FileOp(path=PROVIDERS, text=providers_edit.replace(doc, provider, canonical, text)))
            else:
                ops.append(FileOp(path=key, text=text))
        return ops

    def purge(self, kind: str, item_id: str, author: str | None = None) -> ChangeResult:
        """Remove an archived item's history for good, only when no stored run ever used it."""
        kind = self._kind(kind)
        result = ChangeResult(kind=kind, id=item_id, action="purge", status="rejected")
        with fsops.catalog_lock(self.lock_path):
            if not any(e.kind == kind and e.id == item_id for e in self.journal.archived()):
                result.issues = [Issue(where=f"{kind} {item_id}", detail="only an archived item can be purged")]
                return result
            runs = sum(v["n"] for v in self.results_by_version(kind, item_id)["versions"])
            if runs:
                result.issues = [Issue(where=f"{kind} {item_id}", detail=f"{runs} stored run(s) used it: it stays "
                                                                         f"archived so their history can be read")]
                return result
            mine = self.journal.entries(kind, item_id)
            keep = {sha for e in self.journal.entries() if (e.kind, e.id) != (kind, item_id)
                    for sha in (*e.parts_before.values(), *e.parts_after.values())}
            for e in mine:
                for sha in (*e.parts_before.values(), *e.parts_after.values()):
                    if sha not in keep:
                        (self.journal.blobs / f"{sha}.txt").unlink(missing_ok=True)
            self.journal.append(author=author or self.author, kind=kind, id=item_id, action="purge",
                                note="history removed (never used by a stored run)")
        result.status = "applied"
        return result

    def sync(self, author: str | None = None) -> list[dict]:
        """Record what changed outside authoring since the last entry: a first baseline for items with no history,
        `external` versions for items whose files differ, and removals."""
        author = author or self.author
        made: list[Entry] = []
        with fsops.catalog_lock(self.lock_path):
            snap = self.snapshot()
            if "crash" in snap:
                return []
            seen = set()
            for kind in (*KINDS, *READ_ONLY_KINDS):
                section, _ = HASH_KEY[kind]
                for item_id in snap.get(section, {}):
                    seen.add((kind, item_id))
                    last = self.journal.latest(kind, item_id)
                    if last is None:
                        made.append(self._baseline(snap, kind, item_id, author))
                        continue
                    parts = self.parts(kind, item_id, snap)
                    refs = self.journal.put_parts(parts)
                    if refs != last.parts_after:
                        made.append(self.journal.append(
                            author=author, kind=kind, id=item_id, action="external",
                            version=last.version + 1, hash_before=last.hash_after,
                            hash_after=self._hash(snap, kind, item_id), parts_before=last.parts_after,
                            parts_after=refs, source="external", note="changed outside authoring (editor, git)",
                            recipe_hashes=self._dependents(snap, kind, item_id)))
            for e in self.journal.entries():
                key = (e.kind, e.id)
                if key in seen or e.action not in ("baseline", "create", "update", "restore", "external", "dependency"):
                    continue
                last = self.journal.latest(*key)
                if last is not None and last.seq == e.seq and last.parts_after:
                    made.append(self.journal.append(
                        author=author, kind=e.kind, id=e.id, action="external", version=last.version + 1,
                        hash_before=last.hash_after, parts_before=last.parts_after, source="external",
                        note="removed outside authoring"))
                    seen.add(key)
        return [summary(e) for e in made]

    # ---- results per version -------------------------------------------------------------------------------

    def _rows(self) -> list[dict]:
        if not self.results_url:
            return []
        if self.results_url.startswith("sqlite:///") and not Path(self.results_url.removeprefix("sqlite:///")).is_file():
            return []
        try:
            from harness_factory.results.store import UNSCORED, ResultsStore
            return [r for r in ResultsStore(self.results_url).rows(latest=True) if r["status"] not in UNSCORED]
        except Exception:  # noqa: BLE001  (no store, or an unreachable one: no results to show)
            return []

    def results_by_version(self, kind: str, item_id: str) -> dict[str, Any]:
        """Scored runs grouped by the version of this item they ran with: n, mean E0, SUCCESS share, per recipe."""
        kind = self._kind(kind, read_only=True)
        versions = self.journal.versions(kind, item_id)
        rows = self._rows()
        groups: dict[int | str, list[dict]] = {}

        def put(key, r):
            groups.setdefault(key, []).append(r)

        if kind in ("recipe", "prompt", "skill"):
            owner: dict[tuple[str, str], int] = {}
            for e in versions:
                for rid, h in e.recipe_hashes.items():
                    owner[(rid, h)] = e.version
                if kind == "recipe" and e.hash_after:
                    owner[(item_id, e.hash_after)] = e.version
            for r in rows:
                v = owner.get((r["recipe_id"], r["recipe_hash"]))
                if v is not None:
                    put(v, r)
                elif kind == "recipe" and r["recipe_id"] == item_id:
                    put("before history", r)
        elif kind in ("fault", "task"):
            idx = 6 if kind == "fault" else 1
            owner_h = {e.hash_after: e.version for e in versions if e.hash_after}
            for r in rows:
                parts = r["run_key"].split("|")
                if kind == "fault" and r.get("fault") != item_id:
                    continue
                if kind == "task" and not r["task_version"].startswith(f"{item_id}@"):
                    continue
                v = owner_h.get(parts[idx])
                put(v if v is not None else "before history", r)
        else:                                          # model, server: by time, between version entries
            provider, _, canonical = item_id.partition("/")
            for r in rows:
                if kind == "model" and (r["provider"] != provider or r["canonical_model"] != canonical):
                    continue
                if kind == "server" and r["recipe_id"] not in self._dependents(self.snapshot(), "server", item_id):
                    continue
                at = r["created_at"].strftime("%Y-%m-%dT%H:%M:%SZ") if hasattr(r["created_at"], "strftime") \
                    else str(r["created_at"])
                v = next((e.version for e in reversed(versions) if e.at <= at), None)
                put(v if v is not None else "before history", r)

        def stats(rs: list[dict]) -> dict:
            return {"n": len(rs), "mean_e0": round(statistics.fmean(x["composite"] for x in rs), 4),
                    "success": round(sum(x["status"] == "SUCCESS" for x in rs) / len(rs), 3),
                    "tokens": round(statistics.fmean(x["tokens_in"] + x["tokens_out"] for x in rs))}

        out = []
        for key, rs in groups.items():
            by: dict[str, list[dict]] = {}
            for r in rs:
                by.setdefault(r["recipe_id"] if kind != "recipe" else r["canonical_model"], []).append(r)
            out.append({"version": key, **stats(rs), "by": {k: stats(v) for k, v in sorted(by.items())}})
        out.sort(key=lambda x: (isinstance(x["version"], str), x["version"] if isinstance(x["version"], int) else 0))
        return {"kind": kind, "id": item_id, "grouped_by": "recipe" if kind != "recipe" else "model",
                "versions": out}

    # ---- editable payloads ---------------------------------------------------------------------------------

    def payload(self, kind: str, item_id: str) -> dict[str, Any]:
        """The item as the create/update payload of its kind: what an editor starts from."""
        from harness_factory.authoring.service import AuthoringError
        kind = self._kind(kind)
        snap = self.snapshot()
        if not self._exists(snap, kind, item_id):
            raise AuthoringError(f"no {kind} {item_id!r}")
        parts = self.parts(kind, item_id, snap)
        if kind == "prompt":
            return {"ref": item_id, "text": next(iter(parts.values()), "")}
        if kind == "skill":
            from harness_factory.skills.library import parse_skill
            s = parse_skill(item_id, next(iter(parts.values())))
            return {"id": item_id, "name": s.name, "description": s.description, "body": s.body}
        if kind == "recipe":
            text = next(iter(parts.values()))
            data = yaml.safe_load(text) or {}
            lead = []                       # only the leading comment block is the note
            for line in text.splitlines():
                if not line.startswith("#"):
                    break
                lead.append(line.lstrip("#").strip())
            note = " ".join(lead)
            return {"id": item_id, "base": data.get("base"), "note": note,
                    "fields": {k: v for k, v in data.items() if k not in ("id", "base")}}
        if kind == "fault":
            data = yaml.safe_load(next(iter(parts.values()))) or {}
            return {"id": item_id, "description": data.get("description", ""), "rules": data.get("rules", [])}
        if kind == "server":
            entry = yaml.safe_load(parts[f"{TOOLS}#{item_id}"])
            tools = [{"name": t["name"], "side_effect": t["side_effect"], "risk": t["risk"],
                      "timeout_s": t.get("timeout_s", 10.0), **({"granted_by": t["granted_by"]}
                                                                  if t.get("granted_by") is not None else {})}
                     for t in entry.get("tools", [])]
            out = {"id": item_id, "transport": entry.get("transport", "stdio"), "tools": tools}
            for k in ("url", "version", "auth_env", "tenant"):
                if entry.get(k) is not None:
                    out[k] = entry[k]
            agents = {t["name"]: t["agent"] for t in entry.get("tools", []) if t.get("agent")}
            if agents:
                out["agents"] = agents
            if toolcode.module_path(item_id) in parts:
                out["code"] = parts[toolcode.module_path(item_id)]
            return out
        if kind == "model":
            entry = yaml.safe_load(parts[f"{PROVIDERS}#{item_id}"])[0]
            provider, _, _ = item_id.partition("/")
            caps = {"tool_calling": "native", "structured_output": True, "context_len": 32_768,
                    **(entry.get("caps") or {})}
            return {"provider": provider, "canonical": entry["canonical"],
                    "provider_model_id": entry["provider_model_id"], "price_in": entry.get("price_in", 0.0),
                    "price_out": entry.get("price_out", 0.0), "free": entry.get("free", False), "caps": caps,
                    **({"limits": entry["limits"]} if entry.get("limits") else {}),
                    **({"endpoint": entry["endpoint"]} if entry.get("endpoint") is not None else {})}
        raise AuthoringError(f"no payload for {kind}")

    # ---- models (catalog/providers.yaml) -------------------------------------------------------------------

    def _model_ops(self, item, snap: dict, exists: bool):
        text = (self.root / PROVIDERS).read_text(encoding="utf-8")
        where = f"model {item.id}"
        if item.provider not in providers_edit.providers_in(text):
            return [], [Issue(where=where, detail=f"no provider {item.provider!r} in providers.yaml; a new provider "
                                                  f"(limits, key variable, data terms) is added there by hand")], []
        old = providers_edit.entry_text(text, item.provider, item.canonical)
        if old is not None:
            indent = old[:len(old) - len(old.lstrip())]
            comment = providers_edit.trailing_comment(old)
        else:
            where_end = providers_edit.provider_end(text, item.provider)
            if where_end is None:
                return [], [Issue(where=where, detail=f"provider {item.provider!r} has no models list")], []
            indent, comment = where_end[1], ""
        line = providers_edit.render(providers_edit.compact(item.model_dump(mode="json")), indent, comment)
        notes = ["prices are not part of any hash: editing them changes no identity"] if old else []
        return [FileOp(path=PROVIDERS, text=providers_edit.replace(text, item.provider, item.canonical, line))], [], notes

    def _model_delete_ops(self, item_id: str) -> list[FileOp]:
        provider, _, canonical = item_id.partition("/")
        text = (self.root / PROVIDERS).read_text(encoding="utf-8")
        return [FileOp(path=PROVIDERS, text=providers_edit.replace(text, provider, canonical, None))]

    def _stale_models(self, models: list[str]) -> int:
        keys = {tuple(m.split("/", 1)) for m in models}
        return sum(1 for r in self._rows() if (r["provider"], r["canonical_model"]) in keys)
