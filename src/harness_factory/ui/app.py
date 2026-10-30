"""Harness Factory UI (Streamlit). Run with `hfx ui` (or `streamlit run` this file with HF_API_URL set).

Every page calls the API: catalog changes go through the authoring service and its checks, campaigns through the
same plan and preflight as `hfx smoke`. Nothing here decides what is valid.
"""

from __future__ import annotations

import json
from typing import Any

import streamlit as st
import yaml

from harness_factory.ui.client import Api, ApiError

KIND_LABELS = {"prompt": "Prompts", "skill": "Skills", "recipe": "Recipes", "server": "Tool servers",
               "fault": "Fault profiles"}
STATUS_ICON = {"applied": "✅", "dry_run": "🔎", "needs_confirm": "⚠️", "rejected": "⛔"}


# ---- plumbing ------------------------------------------------------------------------------------------------

@st.cache_resource
def api() -> Api:
    return Api()


@st.cache_data(ttl=10, show_spinner=False)
def fetch(path: str, **params: Any) -> Any:
    return api().get(path, **params)


def refresh() -> None:
    fetch.clear()


def change(key: str, method: str, path: str, payload: Any = None, dry_run: bool = False, confirm: bool = False) -> None:
    """Send a catalog change and keep the answer (and the request, for a confirm) under `key`."""
    with st.spinner("Checking with the factory's validators..."):
        status, body = api().send(method, path, json=payload, dry_run=str(dry_run).lower(),
                                  confirm=str(confirm).lower())
    st.session_state[f"result:{key}"] = {"status": status, "body": body, "request": (method, path, payload)}
    if status < 300 and not dry_run:
        refresh()


def change_buttons(key: str, method: str, path: str, payload_fn, save_label: str = "Save") -> None:
    """Check (dry run) and Save, then the answer. A 409 offers to write it anyway, showing what it changes."""
    c1, c2, _ = st.columns([1, 1, 4])
    if c1.button("Check", key=f"check:{key}", help="Validate and show the impact; writes nothing"):
        change(key, method, path, payload_fn(), dry_run=True)
    if c2.button(save_label, key=f"save:{key}", type="primary"):
        change(key, method, path, payload_fn())
    show_result(key)


def show_result(key: str) -> None:
    last = st.session_state.get(f"result:{key}")
    if not last:
        return
    status, body = last["status"], last["body"]
    if not isinstance(body, dict) or "status" not in body:
        detail = body.get("detail", body) if isinstance(body, dict) else body
        st.error(f"{status}: {detail}")
        return
    r = body
    label = {"applied": "Written", "dry_run": "Checks pass (dry run, nothing written)",
             "needs_confirm": "Changes existing identities: not written yet",
             "rejected": "Rejected: nothing written"}[r["status"]]
    box = {"applied": st.success, "dry_run": st.info, "needs_confirm": st.warning, "rejected": st.error}[r["status"]]
    lines = [f"{STATUS_ICON[r['status']]} **{label}** · {r['action']} {r['kind']} `{r['id']}`"]
    lines += [f"- ✗ {i['where']}: {i['detail']}" for i in r["issues"]]
    if r["status"] != "rejected":
        lines += [f"- {f['action']} `{f['path']}`" for f in r["files"]]
        lines += [f"- note: {n}" for n in r["notes"]]
        imp = r["impact"]
        if imp["recipes_added"]:
            lines.append(f"- new recipes: {', '.join(imp['recipes_added'])}")
        if imp["recipes_changed"]:
            lines.append(f"- recipe hash changes: **{', '.join(imp['recipes_changed'])}**")
        if imp["recipes_removed"]:
            lines.append(f"- recipes removed: **{', '.join(imp['recipes_removed'])}**")
        for sid, rs in imp["env_changed"].items():
            lines.append(f"- tool environment of `{sid}` changes for: **{', '.join(rs) or '(no recipe)'}**")
        if imp.get("stale_results"):
            lines.append(f"- {imp['stale_results']} stored run(s) stay on the old identities (kept as history)")
    box("\n".join(lines))
    if r["status"] == "needs_confirm":
        method, path, payload = last["request"]
        if st.button("Confirm and write", key=f"confirm:{key}", type="primary"):
            change(key, method, path, payload, confirm=True)
            st.rerun()
    if r["status"] == "applied" and r["files"]:
        st.caption("Written, not committed: review with `git diff`.")


def yaml_text(data: Any) -> str:
    return yaml.safe_dump(data, sort_keys=False, default_flow_style=None, width=100) if data else ""


def parse_yaml(text: str, what: str) -> Any:
    try:
        return yaml.safe_load(text) if text.strip() else {}
    except yaml.YAMLError as e:
        st.error(f"{what} is not valid YAML: {e}")
        st.stop()


def parse_params(text: str) -> list[dict[str, Any]]:
    """`a:float, b:int` -> stub parameters."""
    out = []
    for part in [p.strip() for p in (text or "").split(",") if p.strip()]:
        name, _, typ = part.partition(":")
        out.append({"name": name.strip(), "type": (typ.strip() or "str")})
    return out


def tools_editor(key: str, rows: list[dict[str, Any]], with_params: bool) -> list[dict[str, Any]]:
    cols = {"name": st.column_config.TextColumn("name", required=True),
            "description": st.column_config.TextColumn("description"),
            "side_effect": st.column_config.SelectboxColumn("side effect", options=["READ", "REVERSIBLE",
                                                                                    "IRREVERSIBLE"], required=True),
            "risk": st.column_config.SelectboxColumn("risk", options=["R0", "R1", "R2", "R3"], required=True),
            "timeout_s": st.column_config.NumberColumn("timeout s", min_value=1, default=10),
            "granted_by": st.column_config.TextColumn("granted_by", help="comma-separated tool refs; blank = none; "
                                                                         "'*' = every task ([])")}
    if with_params:
        cols["params"] = st.column_config.TextColumn("params (for a stub)", help="a:float, b:int")
    edited = st.data_editor(rows, column_config=cols, num_rows="dynamic", key=key, width="stretch")
    tools = []
    for row in edited:
        if not row.get("name"):
            continue
        t: dict[str, Any] = {"name": row["name"], "description": row.get("description") or "",
                             "side_effect": row.get("side_effect") or "READ", "risk": row.get("risk") or "R0",
                             "timeout_s": float(row.get("timeout_s") or 10)}
        g = (row.get("granted_by") or "").strip()
        if g:
            t["granted_by"] = [] if g == "*" else [x.strip() for x in g.split(",") if x.strip()]
        if with_params:
            t["params"] = parse_params(row.get("params") or "")
        tools.append(t)
    return tools


# ---- pages ---------------------------------------------------------------------------------------------------

def page_catalog() -> None:
    st.header("Catalog")
    kind = st.radio("Kind", list(KIND_LABELS), format_func=KIND_LABELS.get, horizontal=True)
    items = fetch(f"/api/catalog/{kind}")
    st.dataframe([{"id": it["id"] + (" (built-in)" if it["builtin"] else ""), "summary": it["summary"],
                   "used by": len(it["used_by"]), "issues": len(it["issues"]), "hash": it["hash"]} for it in items],
                 width="stretch", hide_index=True)
    ids = [it["id"] for it in items]
    if not ids:
        return
    item_id = st.selectbox("Open", ids)
    it = fetch(f"/api/catalog/{kind}/{item_id}")
    st.caption(f"{it.get('path') or ''} · hash {it.get('hash')}")
    if it["used_by"]:
        st.write("**Used by:** " + ", ".join(it["used_by"]))
    for i in it["issues"]:
        st.error(i)
    language = {"prompt": "jinja2", "skill": "markdown", "recipe": "yaml", "server": "yaml", "fault": "yaml"}[kind]
    if it.get("text"):
        st.code(it["text"], language=language)
    if it["detail"].get("code"):
        st.code(it["detail"]["code"], language="python")

    if it["builtin"]:
        st.info("A built-in server: the factory's code depends on it, so it is edited in the repository.")
        return
    with st.expander("Edit"):
        edit_form(kind, it)
    with st.expander("Delete"):
        st.write("Refused while anything uses it." if it["used_by"] else "Nothing uses it.")
        change_buttons(f"del:{kind}:{item_id}", "DELETE", f"/api/catalog/{kind}/{item_id}", lambda: None,
                       save_label="Delete")


def _skill_parts(text: str) -> tuple[str, str]:
    header, _, body = text[4:].partition("\n---\n") if text.startswith("---\n") else ("", "", text)
    return header, body.strip()


def edit_form(kind: str, it: dict[str, Any]) -> None:
    key, path = f"edit:{kind}:{it['id']}", f"/api/catalog/{kind}/{it['id']}"
    if kind == "prompt":
        text = st.text_area("Template", it.get("text") or "", height=260, key=f"{key}:text")
        change_buttons(key, "PUT", path, lambda: {"ref": it["id"], "text": text})
    elif kind == "skill":
        header, body = _skill_parts(it.get("text") or "")
        meta = yaml.safe_load(header) or {}
        name = st.text_input("Name", meta.get("name", ""), key=f"{key}:name")
        desc = st.text_input("Description", meta.get("description", ""), key=f"{key}:desc")
        body = st.text_area("How-to", body, height=260, key=f"{key}:body")
        change_buttons(key, "PUT", path, lambda: {"id": it["id"], "name": name, "description": desc, "body": body})
    elif kind == "recipe":
        raw = yaml.safe_load(it.get("text") or "") or {}
        note = " ".join(ln[2:] for ln in (it.get("text") or "").splitlines() if ln.startswith("# "))
        base = raw.pop("base", None)
        raw.pop("id", None)
        st.caption(f"Base: {base or '(none: a base recipe)'}. Saving replaces the whole file.")
        note = st.text_input("Note (file comment)", note, key=f"{key}:note")
        fields_text = st.text_area("Fields (YAML)", yaml_text(raw), height=260, key=f"{key}:fields")
        change_buttons(key, "PUT", path, lambda: {"id": it["id"], "base": base, "note": note,
                                                  "fields": parse_yaml(fields_text, "Fields")})
    elif kind == "server":
        d = it["detail"]
        rows = [{"name": t["name"], "description": "", "side_effect": t["side_effect"], "risk": t["risk"],
                 "timeout_s": 10, "granted_by": "*" if t["granted_by"] == [] else ",".join(t["granted_by"] or [])}
                for t in d["tools"]]
        tools = tools_editor(f"{key}:tools", rows, with_params=False)
        if d["transport"] == "stdio":
            code = st.text_area("Server module", d.get("code") or "", height=360, key=f"{key}:code")
            change_buttons(key, "PUT", path, lambda: {"id": it["id"], "transport": "stdio", "tools": tools,
                                                      "code": code})
        else:
            url = st.text_input("URL", d["url"] or "", key=f"{key}:url")
            version = st.text_input("Pinned version", d["version"] or "", key=f"{key}:ver")
            auth = st.text_input("Token env var (name only)", d["auth_env"] or "", key=f"{key}:auth")
            change_buttons(key, "PUT", path, lambda: {"id": it["id"], "transport": "streamable_http", "url": url,
                                                      "version": version, "auth_env": auth or None, "tools": tools,
                                                      **({"tenant": {}} if d["tenant"] else {})})
    elif kind == "fault":
        raw = yaml.safe_load(it.get("text") or "") or {}
        desc = st.text_input("Description", raw.get("description", ""), key=f"{key}:desc")
        rules = st.text_area("Rules (YAML list)", yaml_text(raw.get("rules")), height=200, key=f"{key}:rules")
        change_buttons(key, "PUT", path, lambda: {"id": it["id"], "description": desc,
                                                  "rules": parse_yaml(rules, "Rules") or []})


def page_experiment() -> None:
    st.header("New experiment")
    st.write("A matrix recipe is a base recipe plus **one** change, so a difference in results can be put down "
             "to that change.")
    recipes = {it["id"]: it for it in fetch("/api/catalog/recipe")}
    c1, c2 = st.columns(2)
    base = c1.selectbox("Base recipe", sorted(recipes), index=sorted(recipes).index("v2-base")
                        if "v2-base" in recipes else 0)
    rid = c2.text_input("New recipe id", f"{base}-x", key="exp:id")
    resolved = recipes[base]["detail"]["resolved"]
    dim = st.radio("What changes", ["Prompt", "Skills", "Config", "Tools", "Delegation", "Other (YAML)"],
                   horizontal=True, key="exp:dim")
    note = st.text_input("Note", f"Matrix ({dim.lower()}): ")
    fields: dict[str, Any] = {}
    new_prompt: dict[str, Any] | None = None

    if dim == "Prompt":
        prompts = [it["id"] for it in fetch("/api/catalog/prompt") if it["id"].startswith("system/")]
        mode = st.radio("System prompt", ["Use an existing one", "Write a new one"], horizontal=True)
        if mode == "Use an existing one":
            ref = st.selectbox("Template", prompts)
        else:
            ref = "system/" + st.text_input("Name", "my_prompt")
            st.caption("Rendered with `tools` (the tool names) and `recipe`.")
            text = st.text_area("Template", "You are an automation agent...\n{% if tools %}Available tools: "
                                            "{{ tools | join(', ') }}.{% endif %}\n", height=200)
            new_prompt = {"ref": ref, "text": text}
        fields = {"prompts": {"system": ref}}
    elif dim == "Skills":
        skills = [it["id"] for it in fetch("/api/catalog/skill")]
        chosen = st.multiselect("Skills", skills, default=resolved.get("skills", {}).get("skill_ids") or [])
        loading = st.radio("Loading", ["upfront", "on_demand"], horizontal=True,
                           help="upfront: every how-to in the system prompt; on_demand: an index plus skills.load")
        fields = {"skills": {"skill_ids": chosen, "loading": loading}}
        if loading == "on_demand":
            tools = resolved["tools"]
            fields["tools"] = {"tool_ids": sorted(set(tools["tool_ids"]) | {"skills.*"}),
                               "mcp_servers": sorted(set(tools["mcp_servers"]) | {"skills"})}
            st.caption("on_demand also opens the skills server (a second dimension that the first needs).")
    elif dim == "Config":
        cfg = resolved.get("config", {})
        cols = st.columns(4)
        new = {"max_steps": cols[0].number_input("max_steps", 1, 500, cfg.get("max_steps", 30), key="exp:max_steps"),
               "max_tool_calls": cols[1].number_input("max_tool_calls", 1, 500, cfg.get("max_tool_calls", 50)),
               "max_tokens": cols[2].number_input("max_tokens", 1000, 2_000_000, cfg.get("max_tokens", 200_000),
                                                  step=1000),
               "wall_time_s": cols[3].number_input("wall_time_s", 10, 7200, cfg.get("wall_time_s", 600)),
               "recover_malformed": st.number_input("recover_malformed (0 = off)", 0, 5,
                                                    cfg.get("recover_malformed") or 0)}
        if new["recover_malformed"] == 0 and not cfg.get("recover_malformed"):
            new.pop("recover_malformed")
        fields = {"config": {k: int(v) for k, v in new.items() if v != cfg.get(k)}}
    elif dim == "Tools":
        servers = [it["id"] for it in fetch("/api/catalog/server")]
        chosen = st.multiselect("Servers", servers, default=resolved["tools"]["mcp_servers"])
        patterns = st.text_input("Tool patterns", ", ".join(sorted({f"{s}.*" for s in chosen})),
                                 help="fnmatch patterns over server.tool, e.g. fs.read_file, sql.*")
        fields = {"tools": {"tool_ids": [p.strip() for p in patterns.split(",") if p.strip()],
                            "mcp_servers": chosen}}
    elif dim == "Delegation":
        c = st.columns(3)
        fields = {"delegation": {"recipe": c[0].selectbox("Helper recipe", sorted(recipes)),
                                 "max_calls": int(c[1].number_input("max_calls", 1, 10, 2)),
                                 "max_depth": int(c[2].number_input("max_depth", 1, 3, 1))}}
    else:
        text = st.text_area("Fields to change (YAML)", "config:\n  max_steps: 10\n", height=200)
        fields = parse_yaml(text, "Fields")

    st.write("**Recipe file**")
    st.code(yaml_text({"id": rid, "base": base, **fields}), language="yaml")
    payload = {"id": rid, "base": base, "fields": fields, "note": note}
    if new_prompt is not None:
        st.caption("Saving writes the new prompt first, then the recipe.")
        if st.button("Save prompt and recipe", type="primary"):
            change("exp:prompt", "POST", "/api/catalog/prompt", new_prompt)
            if st.session_state["result:exp:prompt"]["status"] < 300:
                change("exp", "POST", "/api/catalog/recipe", payload)
        show_result("exp:prompt")
        show_result("exp")
    else:
        change_buttons("exp", "POST", "/api/catalog/recipe", lambda: payload, save_label="Create recipe")


def page_add() -> None:
    st.header("Add to the catalog")
    tabs = st.tabs(["Tool server", "Remote server", "Skill", "Prompt", "Fault profile", "Recipe (YAML)"])
    with tabs[0]:
        st.caption("A new capability goes on its own server. It runs in the offline sandbox: import only `mcp` "
                   "and the standard library, and raise `ToolError` for messages the model should see.")
        c1, c2 = st.columns(2)
        sid = c1.text_input("Server id (a Python name)", "my_tools", key="srv:id")
        instr = c2.text_input("Instructions (what the server is for)", "", key="srv:instr")
        tools = tools_editor("srv:tools", [{"name": "", "description": "", "side_effect": "READ", "risk": "R0",
                                            "timeout_s": 10, "granted_by": "", "params": ""}], with_params=True)
        mode = st.radio("Code", ["Generate a stub", "Paste my module"], horizontal=True, key="srv:mode")
        code = None
        if mode == "Paste my module":
            if st.button("Start from a stub", key="srv:stub"):
                status, body = api().send("POST", "/api/servers/stub", json={"id": sid, "instructions": instr,
                                                                            "tools": tools})
                st.session_state["srv:code"] = body.get("code", "") if status == 200 else ""
            code = st.text_area("Server module", height=360, key="srv:code")
        change_buttons("add:server", "POST", "/api/catalog/server",
                       lambda: {"id": sid, "transport": "stdio", "instructions": instr, "tools": tools,
                                **({"code": code} if code else {})})
    with tabs[1]:
        st.caption("An existing MCP server over streamable HTTP. Give the *name* of the env var holding its "
                   "token, never the token. A tool that changes state needs a test tenant.")
        c1, c2, c3 = st.columns(3)
        rid = c1.text_input("Server id", "kb", key="rem:id")
        url = c2.text_input("URL", "https://", key="rem:url")
        ver = c3.text_input("Pinned version", "1", key="rem:ver")
        auth = st.text_input("Token env var", "", key="rem:auth")
        tenant = st.checkbox("Test tenant (needed for tools that change state)", key="rem:tenant")
        rtools = tools_editor("rem:tools", [{"name": "", "description": "", "side_effect": "READ", "risk": "R0",
                                             "timeout_s": 10, "granted_by": ""}], with_params=False)
        change_buttons("add:remote", "POST", "/api/catalog/server",
                       lambda: {"id": rid, "transport": "streamable_http", "url": url, "version": ver,
                                "auth_env": auth or None, "tools": rtools, **({"tenant": {}} if tenant else {})})
    with tabs[2]:
        st.caption("A general how-to, never a task's answer. Its text goes into the hash of every recipe using it.")
        sk = st.text_input("Skill id", "my-skill", key="sk:id")
        name = st.text_input("Name", key="sk:name")
        desc = st.text_input("Description (one line: when it applies)", key="sk:desc")
        body = st.text_area("How-to", "1. \n2. \n", height=200, key="sk:body")
        change_buttons("add:skill", "POST", "/api/catalog/skill",
                       lambda: {"id": sk, "name": name, "description": desc, "body": body})
    with tabs[3]:
        layers = fetch("/api/meta")["prompt_layers"]
        st.caption("Variables per layer: " + "; ".join(f"{k}: {', '.join(v)}" for k, v in layers.items()))
        ref = st.text_input("Ref (<layer>/<name>)", "system/my_prompt", key="pr:ref")
        text = st.text_area("Template", height=200, key="pr:text")
        change_buttons("add:prompt", "POST", "/api/catalog/prompt", lambda: {"ref": ref, "text": text})
    with tabs[4]:
        fid = st.text_input("Profile id", "my-faults", key="ft:id")
        fdesc = st.text_input("Description", key="ft:desc")
        rules = st.text_area("Rules (YAML list)", "- {tools: ['sql.*'], kind: timeout, nth: [1]}\n", key="ft:rules")
        change_buttons("add:fault", "POST", "/api/catalog/fault",
                       lambda: {"id": fid, "description": fdesc, "rules": parse_yaml(rules, "Rules") or []})
    with tabs[5]:
        st.caption("A whole recipe file. Prefer *New experiment* for a matrix recipe.")
        rid2 = st.text_input("Recipe id", "my-recipe", key="rc:id")
        base = st.text_input("Base (blank = a base recipe)", "v2-base", key="rc:base")
        ftext = st.text_area("Fields (YAML)", "config:\n  max_steps: 10\n", height=200, key="rc:fields")
        change_buttons("add:recipe", "POST", "/api/catalog/recipe",
                       lambda: {"id": rid2, "base": base or None, "fields": parse_yaml(ftext, "Fields")})


def campaign_request() -> dict[str, Any]:
    recipes = [it["id"] for it in fetch("/api/catalog/recipe")]
    providers = {p["provider"]: p for p in fetch("/api/models")}
    tasks = fetch("/api/tasks")
    faults = ["none"] + [f["id"] for f in fetch("/api/faults")]
    c1, c2 = st.columns(2)
    chosen = c1.multiselect("Recipes", recipes, default=["v2-base"] if "v2-base" in recipes else recipes[:1])
    provider = c2.selectbox("Provider", list(providers))
    p = providers[provider]
    if not p["key_set"]:
        st.warning(f"{p['auth_env']} is not set in the API's environment: runs on {provider} will end ERROR.")
    models = st.multiselect("Models", [m["canonical"] for m in p["models"]],
                            default=[m["canonical"] for m in p["models"]][:1])
    visible = [t["id"] for t in tasks if t["split"] != "HELD_OUT"]
    picked = st.multiselect("Tasks (blank = every DEV and TEST task)", visible)
    c = st.columns(5)
    seeds = c[0].number_input("Seeds", 1, 20, 1)
    conc = c[1].number_input("Concurrency", 1, 32, 4)
    fsel = c[2].multiselect("Fault profiles", faults, help="add 'none' next to a profile to measure R")
    deadline = c[3].number_input("Deadline days (0 = none)", 0, 60, 0)
    budget = c[4].number_input("Budget USD (0 = none)", 0.0, 1000.0, 0.0)
    resume = st.checkbox("Resume: skip runs that already have a scored result", value=True)
    return {"recipes": chosen, "provider": provider, "models": models or None, "tasks": picked or None,
            "seeds": int(seeds), "concurrency": int(conc), "faults": fsel or None, "resume": resume,
            "deadline_days": int(deadline) or None, "budget_usd": float(budget) or None}


def page_run() -> None:
    st.header("Run a campaign")
    req = campaign_request()
    c1, c2, _ = st.columns([1, 1, 4])
    if c1.button("Plan", help="Projection per quota bucket; runs nothing"):
        st.session_state["plan"] = api().send("POST", "/api/campaigns/plan", json=req)
    if c2.button("Start", type="primary"):
        st.session_state["started"] = api().send("POST", "/api/campaigns", json=req)
        st.session_state.pop("plan", None)
    if "plan" in st.session_state:
        status, body = st.session_state["plan"]
        if status != 200:
            st.error(body.get("detail", body) if isinstance(body, dict) else body)
        else:
            st.write(f"**{body['total']}** runs planned: {body['done']} done, {body['retry']} to retry, "
                     f"{body['new']} new → **{body['will_run']}** will run")
            st.dataframe(body["projection"], width="stretch", hide_index=True)
            for w in body["warnings"]:
                st.warning(w)
            for p in body["problems"]:
                st.error(f"Would be refused: {p}")
    if "started" in st.session_state:
        status, body = st.session_state["started"]
        if status == 202:
            st.success(f"Campaign {body['id']} started: {body['plan']['will_run']} runs")
        else:
            detail = body.get("detail", body) if isinstance(body, dict) else body
            st.error(detail.get("problems", detail) if isinstance(detail, dict) else detail)
    campaigns_panel()


@st.fragment(run_every=3)
def campaigns_panel() -> None:
    st.subheader("Campaigns")
    campaigns = api().get("/api/campaigns")
    if not campaigns:
        st.caption("None since the API started (finished runs are under Results).")
    for c in campaigns:
        prog = c["progress"]
        req = c["request"]
        head = f"`{c['id']}` · {', '.join(req['recipes'])} on {req['provider']} · {c['state']}"
        with st.container(border=True):
            st.write(head)
            st.progress(min(1.0, prog["finished"] / max(1, prog["of"])), text=f"{prog['finished']}/{prog['of']} "
                        + "  ".join(f"{k} {v}" for k, v in sorted(prog["statuses"].items())))
            if c.get("error"):
                st.error(c["error"])
            if c.get("result") and c["result"]["errors"]:
                st.error(f"{len(c['result']['errors'])} run(s) errored; first: {c['result']['errors'][0]['error']}")
            if c["state"] == "running" and st.button("Cancel", key=f"cancel:{c['id']}"):
                api().send("DELETE", f"/api/campaigns/{c['id']}")


def page_results() -> None:
    st.header("Results")
    cells = fetch("/api/results/cells")["cells"]
    if not cells:
        st.info("No scored runs yet. Start one under Run.")
    else:
        rows = [{"task class": c["task_class"], "recipe": c["recipe_id"], "model": c["canonical_model"],
                 "provider": c["provider"], "n": c["e"]["n"], "E": round(c["e"]["mean"], 3),
                 "E 95%": f"{c['e']['ci95'][0]:.2f}–{c['e']['ci95'][1]:.2f}", "pass": round(c["e"]["pass_rate"], 2),
                 "E0": round(c["mean_e0"], 3), "C": round(c["mean_c"], 3),
                 "R": None if c["robustness"] is None else round(c["robustness"], 2), "excl.": c["excluded"],
                 "tokens": c["tokens"], "usd": round(c["usd"], 4)} for c in cells]
        recipes = sorted({r["recipe"] for r in rows})
        keep = st.multiselect("Recipes", recipes, default=recipes)
        st.dataframe([r for r in rows if r["recipe"] in keep], width="stretch", hide_index=True)
    with st.expander("Campaign report (matrix recipes against their base, paired)"):
        if st.button("Build report"):
            st.markdown(api().get("/api/report")["markdown"])
    st.subheader("Recent runs")
    runs = fetch("/api/results", limit=200)
    if runs:
        st.dataframe([{"id": r["id"], "task": r["task_version"], "recipe": r["recipe_id"], "model": r["canonical_model"],
                       "fault": r.get("fault"), "status": r["status"], "E0": round(r["composite"], 3),
                       "tools": r["tool_calls"], "tokens": r["tokens_in"] + r["tokens_out"], "at": r["created_at"]}
                      for r in runs], width="stretch", hide_index=True)
        run_id = st.selectbox("Transcript of run", [r["id"] for r in runs])
        if st.button("Show transcript"):
            try:
                t = api().get(f"/api/runs/{run_id}/transcript")
            except ApiError as e:
                st.error(str(e.detail))
                return
            st.write(f"**{t.get('status')}** · output: {t.get('output')!r}")
            for m in t.get("messages") or []:
                role = m.get("role", "assistant")
                with st.chat_message("user" if role in ("user", "system") else "assistant"):
                    st.caption(role)
                    if m.get("content"):
                        st.write(m["content"])
                    for call in m.get("tool_calls") or []:
                        st.code(json.dumps(call, indent=1, default=str), language="json")


PAGES = {"Catalog": page_catalog, "New experiment": page_experiment, "Add": page_add, "Run": page_run,
         "Results": page_results}


def main() -> None:
    st.set_page_config(page_title="Harness Factory", page_icon="🏭", layout="wide")
    problem = api().health()
    st.sidebar.title("Harness Factory")
    st.sidebar.caption(api().base_url)
    if problem:
        st.error(f"Can't reach the API at {api().base_url}: {problem}")
        st.code("hfx serve        # in another terminal, or: hfx ui --serve", language="bash")
        st.stop()
    page = st.sidebar.radio("Page", list(PAGES), key="page")
    if st.sidebar.button("Refresh"):
        refresh()
    issues = fetch("/api/check")
    if issues:
        st.sidebar.error(f"{len(issues)} catalog issue(s)")
        for i in issues[:10]:
            st.sidebar.caption(f"{i['where']}: {i['detail']}")
    try:
        PAGES[page]()
    except ApiError as e:
        st.error(f"API {e.status}: {e.detail}")


main()
