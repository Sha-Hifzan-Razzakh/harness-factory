"""AuthoringService: list, show, create, update and delete catalog items, safely.

Every change goes through the same steps:

1. build the file operations for the item (and, for a local tool server, check its code and discover its tools);
2. stage them in a temporary copy of the catalog;
3. snapshot the current and the staged catalog (in child processes, with the factory's own loaders);
4. refuse the change if the staged catalog has any error or recipe issue the current one doesn't;
5. report the impact: recipes whose hash changes, servers whose environment changes, stored runs that go stale;
6. write it, all files or none, under a lock, unless it is a dry run or it touches an existing identity
   without `confirm`.
"""

from __future__ import annotations

import fnmatch
import json
import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError

from harness_factory import settings
from harness_factory.authoring import fsops, toolcode
from harness_factory.authoring.fsops import FileOp
from harness_factory.authoring.models import (
    KINDS,
    PAYLOADS,
    ChangeResult,
    FaultIn,
    FileChange,
    Impact,
    Issue,
    Item,
    PromptIn,
    RecipeIn,
    ServerIn,
    SkillIn,
)

BUILTIN_SERVERS = frozenset({"fs", "sql", "search", "skills"})    # code depends on them: edit them in the repo
TOOLS_FILE = "catalog/tools.yaml"
DIMENSIONS = ("variant", "tier", "model_slots", "tools", "skills", "prompts", "config", "policies", "memory",
              "retrieval", "workflow", "delegation", "planning")


class AuthoringError(Exception):
    """A request that can't be turned into a change at all (unknown kind or id, bad payload)."""


def _dump_yaml(data: Any) -> str:
    return yaml.safe_dump(data, sort_keys=False, width=110, default_flow_style=None, allow_unicode=True)


def _comment(text: str) -> str:
    text = " ".join(text.split())
    return "".join(f"# {line}\n" for line in textwrap.wrap(text, 108)) if text else ""


class AuthoringService:
    def __init__(self, root: Path | None = None, results_url: str | None = None,
                 lock_path: Path | None = None) -> None:
        self.root = (root or settings.ROOT).resolve()
        own = self.root == settings.ROOT.resolve()
        self.results_url = results_url if results_url is not None else (settings.RESULTS_URL if own else None)
        self.lock_path = lock_path or ((settings.VAR if own else self.root / "var") / "authoring.lock")
        self._cache: tuple[str, dict[str, Any]] | None = None

    # ---- snapshots -------------------------------------------------------------------------------------------

    def _start_snapshot(self, root: Path, out: Path) -> subprocess.Popen:
        env = {**os.environ, "HF_ROOT": str(root), "HF_VAR": str(out.parent / "var")}
        return subprocess.Popen([sys.executable, "-m", "harness_factory.authoring.snapshot", str(out)],
                                cwd=str(root), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    @staticmethod
    def _finish_snapshot(proc: subprocess.Popen, out: Path) -> dict[str, Any]:
        _, err = proc.communicate(timeout=300)
        if not out.is_file():
            return {"crash": f"snapshot process exited {proc.returncode}: {err.decode(errors='replace')[-2000:]}"}
        return json.loads(out.read_text())

    def _fingerprint(self) -> str:
        """Changes whenever anything a snapshot reads changes: catalog, prompts, or the factory's own code."""
        from harness_factory.hashing import content_hash
        code = Path(__file__).resolve().parents[1]
        stats = []
        for base, pattern in ((self.root / "catalog", "*"), (self.root / "prompts", "*"), (code, "*.py")):
            for p in sorted(base.rglob(pattern)) if base.is_dir() else []:
                if p.is_file():
                    st = p.stat()
                    stats.append((str(p), st.st_size, st.st_mtime_ns))
        return content_hash(stats)

    def snapshot(self) -> dict[str, Any]:
        """The current catalog as the factory sees it. Cached until a file it depends on changes."""
        fp = self._fingerprint()
        if self._cache is not None and self._cache[0] == fp:
            return self._cache[1]
        with tempfile.TemporaryDirectory(prefix="hf-snap-") as d:
            out = Path(d) / "snap.json"
            snap = self._finish_snapshot(self._start_snapshot(self.root, out), out)
        if "crash" not in snap:
            self._cache = (fp, snap)
        return snap

    def _staged_snapshot(self, staged_root: Path) -> dict[str, Any]:
        out = staged_root.parent / "staged.json"
        return self._finish_snapshot(self._start_snapshot(staged_root, out), out)

    # ---- reading ---------------------------------------------------------------------------------------------

    @staticmethod
    def used_by(snap: dict, kind: str, item_id: str) -> list[str]:
        recipes = snap.get("recipes", {})
        if kind == "prompt":
            return [f"recipe {r}" for r, v in recipes.items() if item_id in v["prompt_refs"]]
        if kind == "skill":
            return [f"recipe {r}" for r, v in recipes.items() if item_id in v["skill_ids"]]
        if kind == "recipe":
            out = [f"recipe {r} (base)" for r, v in recipes.items() if v.get("base") == item_id]
            out += [f"recipe {r} (delegate)" for r, v in recipes.items() if v.get("delegate") == item_id]
            return out + (["catalog/selector.yaml"] if item_id in snap.get("selector_refs", []) else [])
        if kind == "server":
            out = [f"recipe {r}" for r, v in recipes.items() if item_id in v["servers"]]
            server = snap.get("servers", {}).get(item_id)
            refs = [f"{item_id}.{t['name']}" for t in server["tools"]] if server else []
            out += [f"task {t}" for t, v in snap.get("tasks", {}).items()
                    if any(fnmatch.fnmatch(ref, pat) for pat in v["allowed_tools"] for ref in refs)]
            out += [f"server {s} (granted_by)" for s, v in snap.get("servers", {}).items() if s != item_id
                    and any(g.split(".")[0] == item_id for t in v["tools"] for g in (t["granted_by"] or []))]
            return out
        return []

    def _items(self, snap: dict, kind: str) -> list[Item]:
        errors: dict[tuple[str, str], list[str]] = {}
        for e in snap.get("errors", []):
            errors.setdefault((e["kind"], e["id"]), []).append(e["detail"])
        out: list[Item] = []
        if kind == "prompt":
            for ref, v in snap.get("prompts", {}).items():
                out.append(Item(kind=kind, id=ref, path=v["path"], hash=v["hash"],
                                summary=f"vars: {', '.join(v['vars']) or '-'}", issues=errors.get(("prompt", ref), [])))
        elif kind == "skill":
            for sid, v in snap.get("skills", {}).items():
                out.append(Item(kind=kind, id=sid, path=v["path"], hash=v["hash"], summary=v["description"],
                                detail={"name": v["name"]}))
        elif kind == "recipe":
            for rid, v in snap.get("recipes", {}).items():
                base = f"base {v['base']}, changes {', '.join(v['changes'])}" if v.get("base") else "base recipe"
                out.append(Item(kind=kind, id=rid, path=v.get("path"), hash=v["hash"],
                                summary=f"{v['variant']} · {base}",
                                issues=[f"{i['rule']}: {i['detail']}" for i in v["issues"]],
                                detail={"base": v.get("base"), "variant": v["variant"], "tier": v["tier"],
                                        "servers": v["servers"], "skill_ids": v["skill_ids"],
                                        "prompt_refs": v["prompt_refs"], "resolved": v["resolved"]}))
        elif kind == "server":
            for sid, v in snap.get("servers", {}).items():
                path = toolcode.module_path(sid) if v["transport"] == "stdio" else None
                where = v["url"] if v["transport"] != "stdio" else v["module"]
                out.append(Item(kind=kind, id=sid, path=path, hash=v["env"], builtin=sid in BUILTIN_SERVERS,
                                summary=f"{v['transport']} {where} · {', '.join(t['name'] for t in v['tools'])}",
                                issues=[f"no schema_hash: {r}" for r in v["unhashed"]], detail=v))
        elif kind == "fault":
            for fid, v in snap.get("faults", {}).items():
                out.append(Item(kind=kind, id=fid, path=self._fault_path(fid), hash=v["hash"],
                                summary=v["description"]))
        for it in out:
            it.used_by = self.used_by(snap, kind, it.id)
        return out

    def list(self, kind: str | None = None, snap: dict | None = None) -> list[Item]:
        snap = snap or self.snapshot()
        if "crash" in snap:
            raise AuthoringError(snap["crash"])
        kinds = [self._kind(kind)] if kind else list(KINDS)
        return [it for k in kinds for it in self._items(snap, k)]

    def get(self, kind: str, item_id: str, snap: dict | None = None) -> Item:
        kind = self._kind(kind)
        for it in self.list(kind, snap):
            if it.id == item_id:
                if kind == "server":
                    it.text = _dump_yaml(self._tools_entry(item_id))
                    module = self.root / toolcode.module_path(item_id)
                    if it.detail.get("transport") == "stdio" and module.is_file():
                        it.detail["code"] = module.read_text()
                elif it.path and (self.root / it.path).is_file():
                    it.text = (self.root / it.path).read_text()
                return it
        raise AuthoringError(f"no {kind} {item_id!r}")

    def errors(self, snap: dict | None = None) -> list[Issue]:
        """Everything wrong with the current catalog: load errors and recipe issues."""
        snap = snap or self.snapshot()
        if "crash" in snap:
            return [Issue(where="catalog", detail=snap["crash"])]
        out = [Issue(where=f"{e['kind']} {e['id']}", detail=e["detail"]) for e in snap["errors"]]
        out += [Issue(where=f"recipe {r}", detail=f"{i['rule']}: {i['detail']}")
                for r, v in snap["recipes"].items() for i in v["issues"]]
        return out

    # ---- writing ---------------------------------------------------------------------------------------------

    def create(self, kind: str, payload: dict | BaseModel, dry_run: bool = False, confirm: bool = False) -> ChangeResult:
        return self._change(kind, payload, "create", dry_run, confirm)

    def update(self, kind: str, payload: dict | BaseModel, dry_run: bool = False, confirm: bool = False) -> ChangeResult:
        return self._change(kind, payload, "update", dry_run, confirm)

    def delete(self, kind: str, item_id: str, dry_run: bool = False, confirm: bool = False) -> ChangeResult:
        kind = self._kind(kind)
        with fsops.catalog_lock(self.lock_path):
            snap = self.snapshot()
            result = ChangeResult(kind=kind, id=item_id, action="delete", status="rejected")
            if "crash" in snap:
                result.issues = [Issue(where="catalog", detail=snap["crash"])]
                return result
            if not any(it.id == item_id for it in self._items(snap, kind)):
                raise AuthoringError(f"no {kind} {item_id!r}")
            if kind == "server" and item_id in BUILTIN_SERVERS:
                result.issues = [Issue(where=f"server {item_id}", detail="a built-in server; edit it in the repo")]
                return result
            users = self.used_by(snap, kind, item_id)
            if users:
                result.issues = [Issue(where=f"{kind} {item_id}", detail=f"still used by {', '.join(users)}")]
                return result
            return self._stage_and_apply(result, snap, self._delete_ops(kind, item_id, snap), dry_run, confirm)

    def _change(self, kind: str, payload: dict | BaseModel, action: str, dry_run: bool, confirm: bool) -> ChangeResult:
        kind = self._kind(kind)
        model = PAYLOADS[kind]
        try:
            item = payload if isinstance(payload, model) else model.model_validate(
                payload.model_dump() if isinstance(payload, BaseModel) else payload)
        except ValidationError as e:
            raise AuthoringError(f"invalid {kind}: {e}") from e
        item_id = item.ref if isinstance(item, PromptIn) else item.id
        with fsops.catalog_lock(self.lock_path):
            snap = self.snapshot()
            result = ChangeResult(kind=kind, id=item_id, action=action, status="rejected")
            if "crash" in snap:
                result.issues = [Issue(where="catalog", detail=snap["crash"])]
                return result
            exists = any(it.id == item_id for it in self._items(snap, kind))
            if action == "create" and exists:
                result.issues = [Issue(where=f"{kind} {item_id}", detail="already exists; update it instead")]
                return result
            if action == "update" and not exists:
                raise AuthoringError(f"no {kind} {item_id!r} to update")
            builder = getattr(self, f"_{kind}_ops")
            ops, issues, notes = builder(item, snap, exists)
            result.notes += notes
            if issues:
                result.issues = issues
                return result
            return self._stage_and_apply(result, snap, ops, dry_run, confirm)

    def _stage_and_apply(self, result: ChangeResult, cur: dict, ops: list[FileOp], dry_run: bool,
                         confirm: bool) -> ChangeResult:
        """`cur` is the snapshot taken under the same lock, so nothing changed the catalog in between."""
        result.files = [FileChange(path=op.path, action=op.action) for op in ops]
        with tempfile.TemporaryDirectory(prefix="hf-stage-") as d:
            staged = Path(d) / "repo"
            fsops.stage(self.root, staged, [op for op in ops if not op.path.startswith("src/")])
            new = self._staged_snapshot(staged)
        if "crash" in new:
            result.issues = [Issue(where="catalog", detail=new["crash"])]
            return result
        result.issues = self._new_issues(cur, new)
        result.impact = self._impact(cur, new)
        if result.issues:
            result.status = "rejected"
        elif dry_run:
            result.status = "dry_run"
        elif result.impact.touches_existing and not confirm:
            result.status = "needs_confirm"
        else:
            fsops.apply(self.root, ops)
            result.status = "applied"
        return result

    @staticmethod
    def _new_issues(cur: dict, new: dict) -> list[Issue]:
        before = [json.dumps(e, sort_keys=True) for e in cur.get("errors", [])]
        out = [Issue(where=f"{e['kind']} {e['id']}", detail=e["detail"])
               for e in new.get("errors", []) if json.dumps(e, sort_keys=True) not in before]
        for rid, v in new.get("recipes", {}).items():
            old = cur.get("recipes", {}).get(rid, {}).get("issues", [])
            out += [Issue(where=f"recipe {rid}", detail=f"{i['rule']}: {i['detail']}")
                    for i in v["issues"] if i not in old]
        return out

    def _impact(self, cur: dict, new: dict) -> Impact:
        cr, nr = cur.get("recipes", {}), new.get("recipes", {})
        imp = Impact(recipes_changed=sorted(r for r in cr if r in nr and cr[r]["hash"] != nr[r]["hash"]),
                     recipes_added=sorted(set(nr) - set(cr)), recipes_removed=sorted(set(cr) - set(nr)))
        cs, ns = cur.get("servers", {}), new.get("servers", {})
        for sid, v in cs.items():
            if sid not in ns or ns[sid]["env"] != v["env"]:
                imp.env_changed[sid] = sorted(r for r, rv in cr.items() if sid in rv["servers"])
        affected = set(imp.recipes_changed) | set(imp.recipes_removed) | {r for rs in imp.env_changed.values()
                                                                          for r in rs}
        imp.stale_results = self._stale(cr, affected) if affected else 0
        return imp

    def _stale(self, recipes: dict, affected: set[str]) -> int | None:
        url = self.results_url
        if not url:
            return None
        if url.startswith("sqlite:///") and not Path(url.removeprefix("sqlite:///")).is_file():
            return 0
        try:
            from harness_factory.results.store import ResultsStore
            rows = ResultsStore(url).rows(latest=True)
        except Exception:  # noqa: BLE001  (no store, or an unreachable one: the impact just has no count)
            return None
        return sum(1 for r in rows if r["recipe_id"] in affected and r["recipe_hash"] == recipes[r["recipe_id"]]["hash"])

    # ---- per-kind file operations ----------------------------------------------------------------------------

    @staticmethod
    def _kind(kind: str | None) -> str:
        k = (kind or "").lower().rstrip("s")
        k = {"tool": "server", "remote-tool": "server"}.get(k, k)
        if k not in KINDS:
            raise AuthoringError(f"unknown kind {kind!r}; kinds: {', '.join(KINDS)}")
        return k

    def _prompt_ops(self, item: PromptIn, snap: dict, exists: bool):
        text = item.text if item.text.endswith("\n") else item.text + "\n"
        return [FileOp(path=f"prompts/{item.ref}.j2", text=text)], [], []

    def _skill_ops(self, item: SkillIn, snap: dict, exists: bool):
        header = _dump_yaml({"name": item.name, "description": " ".join(item.description.split())})
        text = f"---\n{header}---\n{item.body.strip()}\n"
        return [FileOp(path=f"catalog/skills/{item.id}/SKILL.md", text=text)], [], []

    def _recipe_ops(self, item: RecipeIn, snap: dict, exists: bool):
        issues, notes = [], []
        fields = dict(item.fields)
        for k in ("id", "base"):
            if k in fields:
                issues.append(Issue(where=f"recipe {item.id}", detail=f"set `{k}` outside fields"))
        for name, slot in (fields.get("model_slots") or {}).items():
            if isinstance(slot, dict) and slot.get("canonical_model"):
                issues.append(Issue(where=f"recipe {item.id}", detail=f"model_slots.{name}.canonical_model is set; "
                                                                       "leave it empty (the model is a campaign axis)"))
        unknown = [k for k in fields if k not in DIMENSIONS and k not in ("version", "headless", "grader_hooks")]
        if unknown:
            issues.append(Issue(where=f"recipe {item.id}", detail=f"unknown field(s) {', '.join(unknown)}"))
        if item.base is not None:
            if item.base not in snap["recipes"]:
                issues.append(Issue(where=f"recipe {item.id}", detail=f"base {item.base!r} is not a recipe"))
            dims = [k for k in fields if k in DIMENSIONS]
            if not dims:
                issues.append(Issue(where=f"recipe {item.id}", detail="changes nothing from its base"))
            elif len(dims) > 1:
                notes.append(f"changes {len(dims)} dimensions ({', '.join(dims)}): a difference in results can't be "
                             f"put down to one of them (fine when one needs the other, e.g. skills on demand + tools)")
        existing = snap["recipes"].get(item.id, {}).get("path")
        path = existing or f"catalog/recipes/{'matrix' if item.base else 'base'}/{item.id.replace('-', '_')}.yaml"
        version = fields.pop("version", None) or (snap["recipes"].get(item.id, {}).get("resolved", {}).get("version")
                                                  if exists else None) or 1
        data = {"id": item.id, "version": version, **({"base": item.base} if item.base else {}), **fields}
        return [FileOp(path=path, text=_comment(item.note) + _dump_yaml(data))], issues, notes

    def _fault_path(self, fault_id: str) -> str:
        for p in sorted((self.root / "catalog/faults").glob("*.yaml")):
            if (yaml.safe_load(p.read_text()) or {}).get("id") == fault_id:
                return p.relative_to(self.root).as_posix()
        return f"catalog/faults/{fault_id.replace('-', '_')}.yaml"

    def _fault_ops(self, item: FaultIn, snap: dict, exists: bool):
        data = {"id": item.id, "description": item.description, "rules": item.rules}
        return [FileOp(path=self._fault_path(item.id), text=_comment(item.description) + _dump_yaml(data))], [], []

    def _tools_doc(self) -> tuple[str, dict]:
        text = (self.root / TOOLS_FILE).read_text()
        header = "".join(line for line in text.splitlines(keepends=True) if line.startswith("#"))
        return header, yaml.safe_load(text)

    def _tools_entry(self, server_id: str) -> dict:
        return next(s for s in self._tools_doc()[1]["servers"] if s["id"] == server_id)

    def _tools_op(self, server_id: str, entry: dict | None) -> FileOp:
        """tools.yaml with one server replaced (in place), added (at the end) or removed (entry None)."""
        header, doc = self._tools_doc()
        servers = doc["servers"]
        idx = next((i for i, s in enumerate(servers) if s["id"] == server_id), None)
        if entry is None:
            servers.pop(idx)
        elif idx is None:
            servers.append(entry)
        else:
            servers[idx] = entry
        return FileOp(path=TOOLS_FILE, text=header + yaml.safe_dump(doc, sort_keys=False, width=120))

    def _server_ops(self, item: ServerIn, snap: dict, exists: bool):
        where = f"server {item.id}"
        if item.id in BUILTIN_SERVERS:
            return [], [Issue(where=where, detail="a built-in server; edit it in the repo")], []
        names = [t.name for t in item.tools]
        if len(set(names)) != len(names):
            return [], [Issue(where=where, detail="duplicate tool names")], []
        ops, issues, notes = self._remote_ops(item) if item.transport == "streamable_http" else self._local_ops(item)
        refs = [f"{item.id}.{t.name}" for t in item.tools]
        allowed = any(fnmatch.fnmatch(r, pat) for v in snap.get("tasks", {}).values() for pat in v["allowed_tools"]
                      for r in refs)
        if not issues and not allowed and all(t.granted_by is None for t in item.tools):
            notes.append(f"no task allows {item.id}.* yet: the model only sees a tool its task allows. Add it to a "
                         f"task's allowed_tools, or declare granted_by on a READ tool that reveals nothing new")
        return ops, issues, notes

    @staticmethod
    def _tool_entry(t, schema_hash: str | None, agent: dict | None = None) -> dict:
        e: dict[str, Any] = {"name": t.name, "side_effect": t.side_effect, "risk": t.risk, "timeout_s": t.timeout_s}
        if t.granted_by is not None:
            e["granted_by"] = list(t.granted_by)
        if agent is not None:
            e["agent"] = agent
        if schema_hash is not None:
            e["schema_hash"] = schema_hash
        return e

    def _remote_ops(self, item: ServerIn):
        where = f"server {item.id}"
        issues = []
        if item.code:
            issues.append(Issue(where=where, detail="a remote server runs elsewhere; `code` is for local servers"))
        if not item.url or not item.version:
            issues.append(Issue(where=where, detail="a remote server needs `url` and a pinned `version`"))
        unknown = sorted(set(item.agents) - {t.name for t in item.tools})
        if unknown:
            issues.append(Issue(where=where, detail=f"agents for undeclared tools: {', '.join(unknown)}"))
        entry: dict[str, Any] = {"id": item.id, "transport": "streamable_http", "url": item.url, "version": item.version}
        if item.auth_env:
            entry["auth_env"] = item.auth_env
        if item.tenant is not None:
            entry["tenant"] = item.tenant
        entry["tools"] = [self._tool_entry(t, None, item.agents.get(t.name)) for t in item.tools]
        notes = [("schema hashes are not set: run `HF_ALLOW_REMOTE=1 hfx tools freeze` (or HF_REMOTE_MODE=replay "
                  "with a recording) before a recipe opens this server")]
        return ([self._tools_op(item.id, entry)] if not issues else []), issues, notes

    def _local_ops(self, item: ServerIn):
        where = f"server {item.id}"
        notes = []
        if item.url or item.version or item.tenant or item.agents:
            return [], [Issue(where=where, detail="url, version, tenant and agents are for remote servers")], []
        code = item.code
        if not code:
            code = toolcode.stub(item.id, item.instructions, item.tools)
            notes.append(f"generated a stub: every tool answers 'not implemented' until you fill in "
                         f"{toolcode.module_path(item.id)} and update this server")
        problems = toolcode.check_code(code)
        if problems:
            return [], [Issue(where=where, detail=p) for p in problems], notes
        lint = self._ruff(code, toolcode.module_path(item.id))
        if lint:
            return [], [Issue(where=where, detail=f"ruff: {line}") for line in lint], notes
        with tempfile.TemporaryDirectory(prefix="hf-tool-") as d:
            module = Path(d) / f"{item.id}_server.py"
            module.write_text(code)
            try:
                schemas = toolcode.discover(module, item.id)
            except BaseException as e:      # a module that doesn't start; KeyboardInterrupt still propagates
                if isinstance(e, KeyboardInterrupt):
                    raise
                return [], [Issue(where=where, detail=f"the server did not start or list its tools: "
                                                     f"{type(e).__name__}: {e}")], notes
        from harness_factory.hashing import content_hash
        declared, served = {t.name for t in item.tools}, set(schemas)
        issues = [Issue(where=where, detail=f"declared but not served: {n}") for n in sorted(declared - served)]
        issues += [Issue(where=where, detail=f"served but not declared (add side effect and risk): {n}")
                   for n in sorted(served - declared)]
        if issues:
            return [], issues, notes
        entry = {"id": item.id, "transport": "stdio", "module": toolcode.module_name(item.id),
                 "tools": [self._tool_entry(t, content_hash(schemas[t.name])) for t in item.tools]}
        text = code if code.endswith("\n") else code + "\n"
        return [FileOp(path=toolcode.module_path(item.id), text=text), self._tools_op(item.id, entry)], [], notes

    def _ruff(self, code: str, filename: str) -> list[str]:
        """CI lints src/, so an authored module must pass the repository's ruff rules. Skipped without ruff."""
        try:
            p = subprocess.run([sys.executable, "-m", "ruff", "check", "--quiet", "--output-format", "concise",
                                "--stdin-filename", filename, "-"], input=code, text=True, capture_output=True,
                               cwd=str(self.root), timeout=60, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return []
        if "No module named ruff" in p.stderr:
            return []
        return [ln.split(":", 1)[-1].strip() for ln in p.stdout.splitlines() if ln.strip()] if p.returncode else []

    def _delete_ops(self, kind: str, item_id: str, snap: dict) -> list[FileOp]:
        if kind == "server":
            ops = [self._tools_op(item_id, None)]
            if snap["servers"][item_id]["transport"] == "stdio" and (self.root / toolcode.module_path(item_id)).is_file():
                ops.append(FileOp(path=toolcode.module_path(item_id), text=None))
            return ops
        item = next(it for it in self._items(snap, kind) if it.id == item_id)
        return [FileOp(path=item.path, text=None)]
