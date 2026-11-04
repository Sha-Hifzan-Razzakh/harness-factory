"""MCP conformance suite (MCP-11): every declared server, checked against the protocol and against its declaration.

Each server in catalog/tools.yaml has a fixture, catalog/conformance/<server>.yaml: the seed its sandbox starts from,
one happy-path call per declared tool (with what the result must show) and at least one refusal the server
documents. A declared server without a fixture fails, and so does a fixture for a server that isn't declared, so a
new server can't skip the suite. The rules, in the order they run:

  fixture             the fixture loads, names only declared tools and calls every declared tool
  initialize          handshake within the deadline; a known protocol version; the tools capability; the server
                      names itself (a local server by its id); a remote server reports its pinned version
  list_tools          discovery equals the declaration, schema and description hashes included (the drift check
                      provisioning uses, mcp.catalog.ToolCatalog.drift) and every tool's hashes are frozen
  tool_names          every tool name is a valid MCP tool name (SEP-986)
  input_schema        every input (and output) schema is a valid JSON Schema; inputs are objects
  call                every happy-path call succeeds with a well-formed result: content, the expected text or
                      keys, and structured content that matches the tool's output schema when it declares one
  unknown_tool        an unknown tool is an MCP error that names it
  invalid_args        missing or wrong-typed arguments (generated from each schema, plus the fixture's) are MCP
                      errors that name the argument
  refusal             each documented refusal is an MCP error with the server's own message (a ToolError: MCP
                      2.x masks any other exception as "Error executing tool <name>")
  usable_after_error  after those errors the server still answers a ping, list_tools and a read
  shutdown            the server exits on its own when the session closes (it doesn't have to be killed)

Local (stdio) servers run in a fresh sandbox of the configured kind (HF_SANDBOX), exactly as in a run. A remote
server is checked live only with HF_ALLOW_REMOTE=1 (otherwise it is reported as skipped, never as passing); replay
can't be checked, since it answers only the calls a live run made.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from harness_factory import settings
from harness_factory.env.mcp.servers import MCPServerSpec, SideEffect, ToolCatalogFile
from harness_factory.env.sandbox import LocalSandbox, make_sandbox
from harness_factory.mcp.catalog import ToolCatalog
from harness_factory.mcp.client import MCPClient
from harness_factory.tasks.model import EnvSeed

FIXTURES_DIR = settings.CATALOG / "conformance"
UNKNOWN_TOOL = "hf_conformance_no_such_tool"
RULES = ("fixture", "initialize", "list_tools", "tool_names", "input_schema", "call", "unknown_tool",
         "invalid_args", "refusal", "usable_after_error", "shutdown")
_WRONG_VALUE = {"string": 12345, "integer": "not a number", "number": "not a number", "boolean": "not a boolean",
                "array": "not a list", "object": "not an object"}


class Expect(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contains: list[str] = []          # substrings of the result's text
    keys: list[str] = []              # keys of the structured result (a JSON object)


class SampleCall(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: str
    args: dict[str, Any] = {}
    expect: Expect = Expect()


class ErrorCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: str
    args: dict[str, Any] = {}
    contains: list[str] = Field(min_length=1)   # what the error message must say


class Fixture(BaseModel):
    """catalog/conformance/<server>.yaml. Seed paths are relative to the fixture file, as a task's are to its folder."""
    model_config = ConfigDict(extra="forbid")
    server: str
    seed: EnvSeed = EnvSeed()
    edits: dict[str, str] = {}        # workspace files written after the seed (uncommitted when the seed is git)
    skills: list[str] = []            # catalog skills installed where the skills server reads them
    tenant_state: Any = None          # a remote server's test tenant starts from this (EXT-07)
    calls: list[SampleCall] = Field(min_length=1)       # happy path: every declared tool at least once
    refusals: list[ErrorCase] = Field(min_length=1)     # documented refusals
    invalid: list[ErrorCase] = []     # invalid-argument cases on top of the generated ones


class Finding(BaseModel):
    rule: str
    passed: bool
    detail: str = ""


class Report(BaseModel):
    server: str
    transport: str = "stdio"
    sandbox: str | None = None
    skipped: str | None = None        # why the server wasn't checked (a remote server without opt-in)
    findings: list[Finding] = []

    @property
    def ok(self) -> bool:
        return self.skipped is None and len(self.findings) == len(RULES) and all(f.passed for f in self.findings)

    def finding(self, rule: str) -> Finding | None:
        return next((f for f in self.findings if f.rule == rule), None)

    def failures(self) -> list[Finding]:
        return [f for f in self.findings if not f.passed]


class FixtureError(Exception):
    pass


def fixture_path(server_id: str, fixtures_dir: Path = FIXTURES_DIR) -> Path:
    return fixtures_dir / f"{server_id}.yaml"


def load_fixture(server_id: str, fixtures_dir: Path = FIXTURES_DIR) -> tuple[Fixture, Path]:
    path = fixture_path(server_id, fixtures_dir)
    if not path.is_file():
        raise FixtureError(f"no conformance fixture {path}: every declared server needs one (seed, one happy-path "
                           f"call per tool, a documented refusal; see the 'New tool' row of CLAUDE.md)")
    try:
        fx = Fixture.model_validate(yaml.safe_load(path.read_text()))
    except (ValidationError, yaml.YAMLError) as e:
        raise FixtureError(f"{path}: {e}") from None
    if fx.server != server_id:
        raise FixtureError(f"{path} is for server {fx.server!r}, not {server_id!r}")
    return fx, path


def stale_fixtures(declared: ToolCatalogFile, fixtures_dir: Path = FIXTURES_DIR) -> list[Path]:
    """Fixtures for servers tools.yaml doesn't declare (renamed or removed): stale, so the suite fails on them."""
    ids = {s.id for s in declared.servers}
    return sorted(p for p in fixtures_dir.glob("*.yaml") if p.stem not in ids) if fixtures_dir.is_dir() else []


def coverage_problems(spec: MCPServerSpec, fx: Fixture) -> list[str]:
    names = {t.name for t in spec.tools}
    used = [c.tool for c in fx.calls]
    out = [f"calls undeclared tool {t!r}" for t in sorted(set(used) - names)]
    out += [f"no happy-path call for {spec.id}.{t}" for t in sorted(names - set(used))]
    out += [f"refusal on undeclared tool {c.tool!r}" for c in fx.refusals + fx.invalid if c.tool not in names]
    return out


class _FixtureSeed(EnvSeed):
    """A fixture's seed. Its web corpus is found in `corpora/` next to the fixtures folder (catalog/corpora for
    catalog/conformance), as a task's is next to the folder of task folders; every other path is the fixture's own."""

    def web_corpus(self, task_dir: Path):
        from harness_factory.env.corpus import CORPORA, load_corpus
        return load_corpus(task_dir.parent / CORPORA / self.corpus) if self.corpus else None


@dataclass
class _Seed:
    """What LocalSandbox.reset reads from a task: a fixture seeds its sandbox the same way."""
    env_seed: EnvSeed
    source_dir: Path
    id: str

    def key(self) -> str:
        return f"conformance:{self.id}"


@dataclass
class _Outcome:
    is_error: bool
    text: str
    structured: Any = None
    content_types: tuple[str, ...] = ()
    raised: str | None = None         # an MCP (JSON-RPC) error instead of an error result

    @property
    def error(self) -> bool:
        return self.is_error or self.raised is not None

    @property
    def message(self) -> str:
        return self.raised if self.raised is not None else self.text


def _masked(tool: str, message: str) -> bool:
    """MCP 2.x shows a non-ToolError exception as exactly this: the server's reason never reaches the model."""
    return message.strip() == f"Error executing tool {tool}"


async def _call(client: MCPClient, name: str, args: dict[str, Any], timeout_s: float) -> _Outcome:
    try:
        res = await asyncio.wait_for(client.session.call_tool(name, args), timeout_s)
    except TimeoutError:
        return _Outcome(is_error=True, text="", raised=f"no answer within {timeout_s:g} s")
    except Exception as e:  # noqa: BLE001  (an MCP error response, or the connection gone: both are findings)
        return _Outcome(is_error=True, text="", raised=f"{type(e).__name__}: {e}")
    texts = [c.text for c in res.content if getattr(c, "type", "") == "text"]
    return _Outcome(is_error=bool(res.is_error), text="\n".join(texts), structured=res.structured_content,
                    content_types=tuple(getattr(c, "type", "?") for c in res.content))


def _json_object(text: str) -> dict:
    """A tool without an output schema returns its JSON object as text."""
    try:
        value = json.loads(text)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _invalid_cases(tools: dict[str, dict]) -> list[tuple[str, dict, str]]:
    """(tool, args, the argument the error must name): a missing required argument, or else a wrong-typed one."""
    out = []
    for name, schema in sorted(tools.items()):
        props = schema.get("properties") or {}
        required = [r for r in schema.get("required") or [] if r in props]
        if required:
            out.append((name, {}, required[0]))
            continue
        for prop, sub in props.items():
            if isinstance(sub, dict) and sub.get("type") in _WRONG_VALUE:
                out.append((name, {prop: _WRONG_VALUE[sub["type"]]}, prop))
                break
    return out


def _schema_problems(where: str, schema: Any, need_object: bool) -> list[str]:
    from jsonschema.exceptions import SchemaError
    from jsonschema.validators import Draft202012Validator, validator_for
    if not isinstance(schema, dict):
        return [f"{where} is not a JSON object"]
    try:
        validator_for(schema, default=Draft202012Validator).check_schema(schema)
    except SchemaError as e:
        return [f"{where} is not a valid JSON Schema: {e.message}"]
    if need_object and schema.get("type") != "object":
        return [f"{where} must be an object schema (type: object), got type {schema.get('type')!r}"]
    return []


class _Checker:
    def __init__(self, spec: MCPServerSpec, declared: ToolCatalogFile, fx: Fixture, client: MCPClient,
                 deadline_s: float) -> None:
        self.spec, self.declared, self.fx, self.client, self.deadline_s = spec, declared, fx, client, deadline_s
        self.problems: dict[str, list[str]] = {}
        self.raw: dict[str, Any] = {}           # tool name -> the server's Tool (input and output schemas)

    def timeout(self, tool: str) -> float:
        return next((t.timeout_s for t in self.spec.tools if t.name == tool), 30.0) + 10

    async def rule(self, name: str, check) -> None:
        try:
            self.problems[name] = await check()
        except Exception as e:  # noqa: BLE001  (the check itself broke on this server: that is the finding)
            self.problems[name] = [f"{type(e).__name__}: {str(e)[:300]}"]

    async def initialize(self) -> list[str]:
        from mcp_types.version import KNOWN_PROTOCOL_VERSIONS
        init, out = self.client.init_result, []
        if str(init.protocol_version) not in KNOWN_PROTOCOL_VERSIONS:
            out.append(f"protocol version {init.protocol_version!r} is not a known MCP version")
        if init.capabilities.tools is None:
            out.append("the server does not declare the tools capability")
        name = getattr(init.server_info, "name", "") or ""
        if not name:
            out.append("the server reports no name")
        elif not self.spec.remote and name != self.spec.id:
            out.append(f"the server names itself {name!r}, but tools.yaml declares it as {self.spec.id!r}")
        if self.spec.remote and not self.client.server_version:
            out.append(f"the server reports no version, so the pin {self.spec.version!r} can't be checked")
        return out

    async def list_tools(self) -> list[str]:
        res = await asyncio.wait_for(self.client.session.list_tools(), self.deadline_s)
        admin = self.spec.tenant.admin_tools() if self.spec.tenant else set()
        self.raw = {t.name: t for t in res.tools if t.name not in admin}
        discovered = await self.client.list_tools()
        drift = ToolCatalog.build(discovered, self.declared, [self.spec.id]).drift()
        out = [f"{d.tool_ref}: {d.problem}" for d in drift]
        frozen = [t.ref(self.spec.id) for t in self.spec.tools if not t.schema_hash or not t.description_hash]
        if frozen:
            out.append(f"hashes not frozen (run `hfx tools freeze`): {', '.join(frozen)}")
        if out:
            out[-1] += " (a changed tool is a new experiment: freeze it, or fix the server)"
        return out

    async def tool_names(self) -> list[str]:
        from mcp.shared.tool_name_validation import validate_tool_name
        out = []
        for name in sorted(self.raw):
            v = validate_tool_name(name)
            if not v.is_valid:
                out.append(f"{name!r}: {'; '.join(v.warnings)}")
        return out

    async def input_schema(self) -> list[str]:
        out = []
        for name, t in sorted(self.raw.items()):
            out += _schema_problems(f"{name} input schema", t.input_schema, need_object=True)
            if t.output_schema is not None:
                out += _schema_problems(f"{name} output schema", t.output_schema, need_object=False)
        return out

    async def call(self) -> list[str]:
        from jsonschema import ValidationError as SchemaValidationError
        from jsonschema import validate
        out = []
        for c in self.fx.calls:
            res = await _call(self.client, c.tool, c.args, self.timeout(c.tool))
            label = f"{c.tool}({json.dumps(c.args)[:80]})"
            if res.error:
                out.append(f"{label} failed: {res.message[:300]}")
                continue
            if not res.content_types and res.structured is None:
                out.append(f"{label} returned no content")
            missing = [s for s in c.expect.contains if s not in res.text]
            if missing:
                out.append(f"{label}: result lacks {missing}; got {res.text[:200]!r}")
            if c.expect.keys:
                have = res.structured if isinstance(res.structured, dict) else _json_object(res.text)
                inner = have.get("result") if set(have) == {"result"} and isinstance(have.get("result"), dict) \
                    else have
                lack = [k for k in c.expect.keys if k not in inner]
                if lack:
                    out.append(f"{label}: structured result lacks keys {lack}")
            schema = getattr(self.raw.get(c.tool), "output_schema", None)
            if schema is not None:
                if res.structured is None:
                    out.append(f"{label}: the tool declares an output schema but returned no structured content")
                else:
                    try:
                        validate(res.structured, schema)
                    except SchemaValidationError as e:
                        out.append(f"{label}: structured result does not match its output schema: {e.message}")
        return out

    async def unknown_tool(self) -> list[str]:
        res = await _call(self.client, UNKNOWN_TOOL, {}, self.deadline_s)
        if not res.error:
            return [f"calling an unknown tool succeeded ({res.text[:120]!r}); it must be an MCP error"]
        if UNKNOWN_TOOL not in res.message:
            return [f"the unknown-tool error doesn't name the tool: {res.message[:200]!r}"]
        return []

    async def _error_case(self, tool: str, args: dict, wanted: list[str], kind: str) -> list[str]:
        res = await _call(self.client, tool, args, self.timeout(tool))
        label = f"{tool}({json.dumps(args)[:80]})"
        if not res.error:
            return [f"{label} succeeded ({res.text[:120]!r}); a {kind} must be an MCP error"]
        if _masked(tool, res.message):
            return [(f"{label}: the error says only {res.message.strip()!r}, so the reason is masked (raise "
                    f"ToolError, never ValueError: MCP 2.x hides other exceptions' messages from the model)")]
        missing = [w for w in wanted if w not in res.message]
        if missing:
            return [f"{label}: the error should say {missing}; it says {res.message[:200]!r}"]
        return []

    async def invalid_args(self) -> list[str]:
        out = []
        schemas = {n: t.input_schema for n, t in self.raw.items() if isinstance(t.input_schema, dict)}
        for tool, args, arg in _invalid_cases(schemas):
            out += await self._error_case(tool, args, [arg], "call with invalid arguments")
        for c in self.fx.invalid:
            out += await self._error_case(c.tool, c.args, c.contains, "call with invalid arguments")
        return out

    async def refusal(self) -> list[str]:
        out = []
        for c in self.fx.refusals:
            out += await self._error_case(c.tool, c.args, c.contains, "documented refusal")
        return out

    async def usable_after_error(self) -> list[str]:
        await asyncio.wait_for(self.client.ping(), self.deadline_s)
        res = await asyncio.wait_for(self.client.session.list_tools(), self.deadline_s)
        if {t.name for t in res.tools} < set(self.raw):
            return ["after the errors, list_tools no longer lists every tool"]
        reads = [c for c in self.fx.calls
                 if any(t.name == c.tool and t.side_effect == SideEffect.READ for t in self.spec.tools)]
        if reads:
            c = reads[0]
            again = await _call(self.client, c.tool, c.args, self.timeout(c.tool))
            if again.error:          # only that it answers: the calls in between may have changed what it shows
                return [f"after the errors, {c.tool} fails: {again.message[:200]}"]
        return []


def _install(sandbox: LocalSandbox, fx: Fixture, source: Path) -> None:
    seed = _FixtureSeed.model_validate(fx.seed.model_dump())
    sandbox.reset(_Seed(env_seed=seed, source_dir=source.parent, id=fx.server))
    for rel, text in fx.edits.items():
        p = (sandbox.workspace / rel).resolve()
        if not p.is_relative_to(sandbox.workspace.resolve()):
            raise FixtureError(f"{source}: edit {rel!r} is outside the workspace")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    if fx.skills:
        from harness_factory.skills.library import load_skills
        lib = load_skills()
        for sid in fx.skills:
            dest = sandbox.skills_dir / sid / "SKILL.md"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(lib[sid].text)


def _finish(report: Report, problems: dict[str, list[str]], why_not: str = "") -> Report:
    for rule in RULES:
        if rule in problems:
            p = problems[rule]
            report.findings.append(Finding(rule=rule, passed=not p, detail="; ".join(p)))
        else:
            report.findings.append(Finding(rule=rule, passed=False, detail=f"not checked: {why_not}"))
    return report


async def check_server(declared: ToolCatalogFile, server_id: str, *, fixtures_dir: Path = FIXTURES_DIR,
                       sandbox_kind: str | None = None, deadline_s: float | None = None) -> Report:
    """Run every rule against one declared server. Never raises for the server's faults: they are findings."""
    spec = declared.server(server_id)
    deadline = deadline_s if deadline_s is not None else settings.health_timeout_s()
    report = Report(server=server_id, transport=spec.transport)
    problems: dict[str, list[str]] = {}
    try:
        fx, source = load_fixture(server_id, fixtures_dir)
    except FixtureError as e:
        return _finish(report, {"fixture": [str(e)]}, "the fixture is missing or invalid")
    problems["fixture"] = coverage_problems(spec, fx)
    if spec.remote and (settings.remote_mode() == "replay" or not settings.allow_remote()):
        report.skipped = ("remote server: checked live only, with HF_ALLOW_REMOTE=1 (replay answers only the "
                          "calls a live run made)")
        return report
    sandbox: LocalSandbox | None = None
    try:
        if not spec.remote:
            sandbox = await asyncio.to_thread(make_sandbox, f"conform-{server_id}", sandbox_kind)
            report.sandbox = sandbox.kind
            try:
                await asyncio.to_thread(_install, sandbox, fx, source)
            except Exception as e:  # noqa: BLE001  (a seed the sandbox can't take is the fixture's fault)
                problems["fixture"].append(f"cannot seed the sandbox: {type(e).__name__}: {str(e)[:300]}")
                return _finish(report, problems, "the sandbox could not be seeded")
        tenant = f"hf-conform-{uuid.uuid4().hex[:12]}" if spec.tenant else None
        client = MCPClient(spec, sandbox.env() if sandbox else {}, sandbox.launch if sandbox else None,
                           tenant=tenant)
        try:
            async with asyncio.timeout(deadline):
                await client.__aenter__()          # a failed or cancelled __aenter__ closes what it opened
        except Exception as e:  # noqa: BLE001
            why = f"no answer within {deadline:g} s" if isinstance(e, TimeoutError) else \
                f"{type(e).__name__}: {str(e)[:300]}"
            problems["initialize"] = [f"the handshake failed: {why}"]
            return _finish(report, problems, "the server did not start")
        closed = False
        try:
            if tenant is not None:
                reset = await client.tenant_reset(fx.tenant_state or {})
                if reset.is_error:
                    problems["fixture"].append(f"the test tenant could not be reset: {reset.text[:200]}")
            chk = _Checker(spec, declared, fx, client, deadline)
            for rule in RULES[1:-1]:
                await chk.rule(rule, getattr(chk, rule))
            problems.update(chk.problems)
            if tenant is not None:
                await client.tenant_drop()
            t0 = time.monotonic()
            closed = True
            await client.__aexit__(None, None, None)     # same task as __aenter__ (anyio scopes); bounded inside
            problems["shutdown"] = _shutdown(spec, client, time.monotonic() - t0)
        finally:
            if not closed:
                await client.__aexit__(None, None, None)
    finally:
        if sandbox is not None:
            await asyncio.shield(asyncio.to_thread(sandbox.teardown))
    return _finish(report, problems)


def _shutdown(spec: MCPServerSpec, client: MCPClient, took_s: float) -> list[str]:
    if spec.remote:
        return [f"closing the session failed: {client.close_error}"] if client.close_error else []
    from mcp.client.stdio import PROCESS_TERMINATION_TIMEOUT
    if took_s >= PROCESS_TERMINATION_TIMEOUT:
        return [(f"the server did not exit when its stdin closed (closing took {took_s:.1f} s; it had to be "
                f"killed after {PROCESS_TERMINATION_TIMEOUT:g} s)")]
    return []


async def check_all(declared: ToolCatalogFile, servers: list[str] | None = None, *,
                    fixtures_dir: Path = FIXTURES_DIR, sandbox_kind: str | None = None,
                    concurrency: int = 4) -> list[Report]:
    """Every declared server (or `servers`), up to `concurrency` at a time, in declaration order."""
    ids = servers or [s.id for s in declared.servers]
    gate = asyncio.Semaphore(max(1, concurrency))

    async def one(sid: str) -> Report:
        async with gate:
            return await check_server(declared, sid, fixtures_dir=fixtures_dir, sandbox_kind=sandbox_kind)

    return list(await asyncio.gather(*(one(s) for s in ids)))
