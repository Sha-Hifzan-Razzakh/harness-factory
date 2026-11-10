"""The guided pages of the UI: Playbook, What we can test with, Cards, and Build an experiment.

Like the rest of the UI they only call the API. `pages(api, fetch, campaigns_panel)` returns them; app.py puts them
before the Advanced pages (Catalog, New experiment, Add, Run, Results)."""

from __future__ import annotations

import itertools
import json
from collections.abc import Callable
from typing import Any

import streamlit as st

MAX_PREVIEW = 60          # spec cards built at once on the Build page

GLOSSARY = [
    ("O (outcome)", "Weighted share of the task's oracle checks that pass on the end state, 0..1."),
    ("Q (trajectory)", ("1 - (invalid + repeated calls) / calls; counted as Q x O so a clean path to a wrong end "
                       "earns nothing.")),
    ("E0", "The run's stored score: the weighted mean of the families; 0 on any critical violation (a guard)."),
    ("C (cost)", "min(cost / task median, 2) / 2: 0 = free, 0.5 = the usual cost, 1 = twice or more."),
    ("E", "max(0, E0 - w_c x C), computed per cell at aggregation; what harnesses are ranked by."),
    ("R (robustness)", "Mean outcome of faulted runs / clean runs, capped at 1; needs both kinds of run."),
    ("RunKey", ("Task, recipe, model, provider model id, tool specs, seed and fault hashed together. Equal key = "
               "same experiment; --resume skips it.")),
    ("Cell", "Task class x recipe x model x provider x environment: what results are averaged over."),
    ("Guard", "A check with weight 0 and critical: true. Earns nothing; failing it zeroes the run."),
    ("Task class", "Expected depth | strongest side effect | risk, e.g. KNOWN_PATH|REVERSIBLE|R1."),
    ("Matrix recipe", "A base recipe plus exactly one change, so a difference in results can be put down to it."),
]

PLAYBOOKS = [
    ("Run one experiment and inspect everything first",
     "See exactly what a run will use before spending anything.",
     ["Build an experiment: pick one task, one harness and one model.",
      ("Build spec cards: read the verdict, the tools the model will see, the prompts it will be sent, the "
      "budgets and the cost estimate."),
      "Start, then Collect final spec cards: the result sits next to its spec and is saved."],
     "hfx card spec --task fs-02 --recipe v2-base --model scripted/replay --provider scripted --run",
     "verdict READY, the 'Tools the model will see' table, Result status and failed checks"),
    ("Compare two harnesses on the same tasks",
     "Find out whether a change to the harness helps, on equal terms.",
     ["What we can test with > Recipes: pick a base and a matrix recipe built on it (one change).",
      "Build an experiment: the tasks of one class, both recipes, one model, 3+ seeds.",
      ("Results: compare E per cell; the campaign report pairs runs by task and seed (mean dE with a 95% "
      "interval, a verdict only when it excludes 0).")],
     "hfx smoke --recipe v2-base,v2-plan-check --models <m> --seeds 3 && hfx report --compare v2-base:v2-plan-check",
     "E and its interval per cell, the paired verdict"),
    ("Try a new model",
     "Check a model is wired up and see how it does before a campaign.",
     ["Cards > model: provider ids, context window, prices, limits; is its API key set?",
      "Build an experiment with scripted/replay first (free), then the real model on 2-3 tasks.",
      "Read the cost estimate on the spec cards; set a budget on Run if needed."],
     "hfx card model gpt-oss-120b && hfx smoke --provider groq --models gpt-oss-120b --dry-run",
     "key set, context window vs max output tokens, projected tokens per day"),
    ("Test robustness",
     "How much of its result a harness keeps when tools misbehave.",
     ["What we can test with > Fault profiles: pick one (first-call-fails is the clearest).",
      "Build an experiment with fault profiles 'none' AND the profile (R needs both).",
      "Results: the R column per cell."],
     "hfx smoke --recipe v2-base,v3-staged --models <m> --faults none,transient --seeds 3",
     "R per cell; faulted runs never enter E"),
    ("Build a new harness variant",
     "Make a matrix recipe: a base plus one change.",
     ["What we can test with > Recipe fields: what can change and the defaults.",
      "Advanced > New experiment: pick the base, the one dimension, its value; Check, then Save.",
      "Cards > harness: confirm 'Changes vs <base>' shows exactly your change."],
     "hfx new recipe v2-terse --set prompts.system=system/terse --note 'Matrix (prompt): terse'",
     "the harness card's change table and its new hash"),
    ("Knowledge tasks (retrieval and web search)",
     "Compare retrieval set-ups and web grounding offline.",
     ["Tasks rag-01/02/03 (60 opaque documents) and web-01/02 (stale pages rank first).",
      "Harnesses v2-retrieval-bm25 vs v2-retrieval-hybrid, and v2-web for the web tasks.",
      "Compare E per recipe; a web answer only counts as grounded when the page was fetched."],
     "hfx smoke --recipe v2-retrieval-bm25,v2-retrieval-hybrid --models scripted/replay",
     "outcome per task and the grounded check"),
    ("Add a tool",
     "Give harnesses a new capability on its own MCP server.",
     ["Advanced > Add > Tool server: declare each tool's side effect and risk.",
      "Cards > tool: schema frozen, conformance fixture present.",
      "Make a matrix recipe that opens the server; build spec cards to see the tool offered."],
     "hfx new tool calc --tool add --param a:float --param b:float && hfx conformance",
     "the tool card's issues list"),
    ("Pick the harness for a task class",
     "Use the measured cells to choose; apply your own strategy on top.",
     ["Run every candidate on the class's tasks with 3+ seeds.",
      "Results: rank by E with its interval; overlapping intervals are a close call.",
      "Record the choice: the winner's final spec card is its evidence."],
     "hfx select show --model <m> --provider <p>",
     "E intervals, pass rate, cost C, R"),
]


def pages(api: Callable, fetch: Callable, campaigns_panel: Callable) -> dict[str, Callable[[], None]]:

    def show_card(card: dict[str, Any], key: str) -> None:
        verdict = card.get("verdict")
        if verdict == "READY":
            st.success(f"READY · {card['summary']}")
        elif verdict == "BLOCKED":
            st.error(f"BLOCKED · {card['summary']}")
        for i in card.get("issues") or []:
            st.warning(i)
        st.markdown(card["markdown"].split("\n", 1)[1] if card["markdown"].startswith("# ") else card["markdown"])
        c1, c2, _ = st.columns([1, 1, 4])
        c1.download_button("Download .md", card["markdown"], file_name=f"{card['kind']}-{card['id']}.md",
                           key=f"dl-md:{key}")
        body = {k: v for k, v in card.items() if k != "markdown"}
        c2.download_button("Download .json", json.dumps(body, indent=2), file_name=f"{card['kind']}-{card['id']}.json",
                           key=f"dl-json:{key}")

    # ---- Playbook -------------------------------------------------------------------------------------------

    def page_playbook() -> None:
        st.header("Playbook")
        st.write("Harness Factory measures **which agent harness works best for which kind of task on which "
                 "model**. It runs a harness (a recipe) on a task in a fresh sandbox, grades what changed in the "
                 "environment, never what the model claims, and stores a score per task class x recipe x model.")
        st.subheader("How every experiment goes")
        st.markdown(
            "1. **Task**: what to do, its seed data and the checks that grade it (*Cards > task*).\n"
            "2. **Harness**: the recipe: shape, prompts, tools, budgets, permissions (*Cards > harness*).\n"
            "3. **Model**: which model on which provider (*Cards > model*).\n"
            "4. **Spec card**: the three combined: the exact tools and prompts the model will see, its RunKey, "
            "budgets, cost estimate, and READY or BLOCKED (*Build an experiment*).\n"
            "5. **Run**: in a fresh sandbox, paced to the provider's limits.\n"
            "6. **Final spec card**: the spec with its result, saved as evidence (*Cards > saved*).\n"
            "7. **Compare**: cells and paired reports (*Advanced > Results*).")
        st.info("Start offline with the scripted models (scripted/replay follows each task's reference solution): "
                "they cost nothing and prove the set-up before any API budget is spent.")
        st.subheader("Playbooks")
        for title, goal, steps, cli, look in PLAYBOOKS:
            with st.expander(title):
                st.write(goal)
                st.markdown("\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1)))
                st.caption("Same thing in the terminal")
                st.code(cli, language="bash")
                st.write(f"**Look at:** {look}")
        st.subheader("Rules that keep the numbers honest")
        st.markdown(
            "- A run that does nothing scores 0; 'don't touch anything else' checks are guards.\n"
            "- Only clean, scored runs are averaged; INCOMPLETE (provider) and ERROR (setup) runs are excluded.\n"
            "- Cost C and robustness R are relative, computed at aggregation; freeze a cost baseline before "
            "comparing campaigns.\n"
            "- Editing a task, recipe, prompt or tool changes its hash: older results stop counting for it.\n"
            "- Use Docker sandboxes (HF_SANDBOX=docker) for real models.")
        st.subheader("Glossary")
        st.dataframe([{"term": t, "meaning": m} for t, m in GLOSSARY], hide_index=True, width="stretch")

    # ---- What we can test with ------------------------------------------------------------------------------

    TABS = [("Tasks", "tasks", "What harnesses are measured on. The class decides which cell a result lands in."),
            ("Shapes", "shapes", "The control flow around the model, the main thing a harness varies."),
            ("Recipes (harnesses)", "recipes", "Base recipes, and matrix recipes = a base plus one change."),
            ("Recipe fields", "recipe_fields", ("Every field a recipe can set, with its default and allowed values: "
                                               "the full menu of dimensions to vary.")),
            ("Models", "models", "Canonical models per provider. 'key set' says whether the API can call it."),
            ("Tools", "tools", "Every declared tool, by MCP server, with its side effect and risk."),
            ("Skills", "skills", "General how-tos a recipe can give the model, upfront or on demand."),
            ("Prompts", "prompts", "Prompt templates by layer, and which recipes use them."),
            ("Fault profiles", "faults", "Tools that fail on purpose, to measure robustness R."),
            ("Runtimes", "runtimes", ("native = the factory's shapes; the others run a framework's own agent "
                                     "(tool-loop recipes only).")),
            ("Check kinds", "check_kinds", "How a task's oracle can grade the end state."),
            ("Perturbations", "perturbations", "Ways a task variant is changed (hfx perturb)."),
            ("Retrieval modes", "retrieval_modes", "retrieval.mode values for the retrieve__search tool.")]

    def page_options() -> None:
        st.header("What we can test with")
        st.write("Everything an experiment can be built from, read live from the catalog. Open any item's card "
                 "below its table.")
        opts = fetch("/api/options")
        counts = {label: len(opts[key]) for label, key, _ in TABS}
        st.caption(" · ".join(f"{k}: {v}" for k, v in counts.items()))
        tabs = st.tabs([label for label, _, _ in TABS])
        card_kind = {"tasks": ("task", "id"), "recipes": ("harness", "id"), "models": ("model", "model"),
                     "tools": ("tool", "server"), "faults": ("fault", "id")}
        for tab, (label, key, help_text) in zip(tabs, TABS):
            with tab:
                st.caption(help_text)
                rows = opts[key]
                if key in ("tasks", "recipes", "tools", "recipe_fields"):
                    q = st.text_input("Filter", key=f"opt-q:{key}", placeholder="type to filter any column")
                    if q:
                        rows = [r for r in rows if q.lower() in " ".join(map(str, r.values())).lower()]
                st.dataframe(rows, hide_index=True, width="stretch")
                if key in card_kind:
                    kind, col = card_kind[key]
                    ids = sorted({str(r[col]) for r in rows})
                    if ids:
                        pick = st.selectbox(f"Open a {kind} card", ids, key=f"opt-pick:{key}", index=None)
                        if pick:
                            show_card(api().get(f"/api/cards/{kind}/{pick}"), f"opt:{key}:{pick}")

    # ---- Cards ----------------------------------------------------------------------------------------------

    def page_cards() -> None:
        st.header("Cards")
        st.write("A card is the spec of one building block. The **final spec card** of an experiment is built on "
                 "*Build an experiment* and saved here once it has run.")
        opts = fetch("/api/options")
        choices = {"Harness": ("harness", [r["id"] for r in opts["recipes"]]),
                   "Model": ("model", sorted({m["model"] for m in opts["models"]})),
                   "Task": ("task", [t["id"] for t in opts["tasks"]]),
                   "Tool server": ("tool", sorted({t["server"] for t in opts["tools"]})),
                   "Fault profile": ("fault", [f["id"] for f in opts["faults"]]),
                   "Saved final spec cards": ("saved", [])}
        label = st.radio("Card", list(choices), horizontal=True, key="cards:kind")
        kind, ids = choices[label]
        if kind == "saved":
            saved = api().get("/api/cards/saved")
            if not saved:
                st.info("No final spec cards yet. Build and run an experiment first.")
                return
            st.dataframe(saved, hide_index=True, width="stretch")
            name = st.selectbox("Open", [s["name"] for s in saved], key="cards:saved")
            if name:
                show_card(api().get(f"/api/cards/saved/{name}"), f"saved:{name}")
            return
        pick = st.selectbox(label, ids, key=f"cards:id:{kind}")
        if pick:
            show_card(api().get(f"/api/cards/{kind}/{pick}"), f"cards:{kind}:{pick}")

    # ---- Build an experiment --------------------------------------------------------------------------------

    def step(n: int, title: str, help_text: str):
        box = st.container(border=True)
        box.subheader(f"Step {n}. {title}")
        box.caption(help_text)
        return box

    def page_build() -> None:
        st.header("Build an experiment")
        st.write("Pick a task, a harness and a model; read their spec cards; run; get the final spec card. "
                 "Nothing runs until Step 6.")
        opts = fetch("/api/options")
        tasks = {t["id"]: t for t in opts["tasks"] if t["split"] != "HELD_OUT"}
        recipes = {r["id"]: r for r in opts["recipes"]}
        providers: dict[str, list[dict]] = {}
        for m in opts["models"]:
            providers.setdefault(m["provider"], []).append(m)

        with step(1, "Tasks", "What to do. Filter by class or domain; each task's card shows its checks."):
            c1, c2 = st.columns(2)
            classes = sorted({t["class"] for t in tasks.values()})
            cls = c1.multiselect("Task class", classes, key="b:cls")
            doms = c2.multiselect("Domain", sorted({t["domain"] for t in tasks.values()}), key="b:dom")
            pool = [t for t in tasks.values() if (not cls or t["class"] in cls) and (not doms or t["domain"] in doms)]
            chosen_tasks = st.multiselect("Tasks", [t["id"] for t in pool], default=["fs-02"] if "fs-02" in tasks else [],
                                          key="b:tasks")
            if chosen_tasks:
                st.dataframe([{k: tasks[t][k] for k in ("id", "class", "domain", "checks", "instruction")}
                              for t in chosen_tasks], hide_index=True, width="stretch")

        with step(2, "Harnesses", "How the model is driven. A matrix recipe changes one thing in its base."):
            chosen_recipes = st.multiselect("Harnesses (recipes)", list(recipes), default=["v2-base"], key="b:recipes")
            if chosen_recipes:
                st.dataframe([{k: recipes[r][k] for k in ("id", "kind", "base", "shape", "runtime", "servers", "changes")}
                              for r in chosen_recipes], hide_index=True, width="stretch")

        with step(3, "Model", "Start with scripted/replay (free, follows the reference solution) to prove the set-up."):
            c1, c2 = st.columns(2)
            plist = list(providers)
            provider = c1.selectbox("Provider", plist, index=plist.index("scripted") if "scripted" in plist else 0,
                                    key="b:provider")
            served = providers[provider]
            models = c2.multiselect("Models", [m["model"] for m in served], default=[served[0]["model"]], key="b:models")
            if served and not served[0]["key set"]:
                st.warning(f"The API key for {provider} is not set in the API's environment: runs would end ERROR.")
            if models:
                st.dataframe([m for m in served if m["model"] in models], hide_index=True, width="stretch")

        with step(4, "Run settings", "Seeds repeat the run; add 'none' next to a fault profile to measure R."):
            c = st.columns(4)
            seeds = int(c[0].number_input("Seeds", 1, 20, 1, key="b:seeds"))
            faults = c[1].multiselect("Fault profiles", ["none"] + [f["id"] for f in opts["faults"]], key="b:faults")
            conc = int(c[2].number_input("Concurrency", 1, 32, 4, key="b:conc"))
            resume = c[3].checkbox("Skip runs already scored", value=True, key="b:resume")

        combos = list(itertools.product(chosen_tasks, chosen_recipes, models, faults or ["none"]))
        with step(5, "Spec cards", f"{len(combos)} combination(s) x {seeds} seed(s). The spec card is what each run "
                                   "will use; BLOCKED ones say why."):
            if not combos:
                st.info("Pick at least one task, harness and model.")
                return
            if len(combos) > MAX_PREVIEW:
                st.warning(f"{len(combos)} combinations: only the first {MAX_PREVIEW} are previewed.")
            if st.button("Build spec cards", type="primary", key="b:preview"):
                st.session_state["b:cards"] = [
                    api().send("POST", "/api/cards/spec", json={"task": t, "recipe": r, "model": m, "provider": provider,
                                                                "fault": fl, "prompts": True})
                    for t, r, m, fl in combos[:MAX_PREVIEW]]
            built = st.session_state.get("b:cards") or []
            cards = [b for s, b in built if s == 200]
            for s, b in built:
                if s != 200:
                    st.error(b.get("detail", b) if isinstance(b, dict) else b)
            if cards:
                st.dataframe([{"task": c["summary"].split(" with ")[0], "harness": c["title"].split(" x ")[1],
                               "model": c["title"].split(" x ")[2], "verdict": c["verdict"], "issues": len(c["issues"]),
                               "run key": c["id"]} for c in cards], hide_index=True, width="stretch")
                for i, c in enumerate(cards):
                    with st.expander(f"{'✅' if c['verdict'] == 'READY' else '⛔'} {c['title']}"):
                        show_card(c, f"b:card:{i}")
        blocked = [c for c in cards if c["verdict"] == "BLOCKED"]
        skipped = [c for c in blocked if all("can't call" in i for i in c["issues"])]
        hard = [c for c in blocked if c not in skipped]

        with step(6, "Run", "Plan shows the projection per quota bucket; Start runs it in the background."):
            if not cards:
                st.info("Build the spec cards first.")
                return
            if skipped:
                st.caption(f"{len(skipped)} pair(s) whose harness can't reach the task's tools will be skipped.")
            if hard:
                st.error(f"{len(hard)} spec card(s) are BLOCKED for another reason: fix them before running.")
            req = {"recipes": chosen_recipes, "provider": provider, "models": models, "tasks": chosen_tasks,
                   "seeds": seeds, "concurrency": conc, "faults": faults or None, "resume": resume}
            c1, c2, _ = st.columns([1, 1, 4])
            if c1.button("Plan", key="b:plan"):
                st.session_state["b:plan-result"] = api().send("POST", "/api/campaigns/plan", json=req)
            if c2.button("Start", type="primary", disabled=bool(hard), key="b:start"):
                st.session_state["b:started-result"] = api().send("POST", "/api/campaigns", json=req)
            if "b:plan-result" in st.session_state:
                s, b = st.session_state["b:plan-result"]
                if s == 200:
                    st.write(f"**{b['total']}** runs planned: {b['done']} done, {b['retry']} to retry, {b['new']} new "
                             f"→ **{b['will_run']}** will run")
                    st.dataframe(b["projection"], hide_index=True, width="stretch")
                    for p in b["problems"]:
                        st.error(f"Would be refused: {p}")
                else:
                    st.error(b.get("detail", b) if isinstance(b, dict) else b)
            if "b:started-result" in st.session_state:
                s, b = st.session_state["b:started-result"]
                if s == 202:
                    st.success(f"Campaign {b['id']} started: {b['plan']['will_run']} runs")
                else:
                    d = b.get("detail", b) if isinstance(b, dict) else b
                    st.error(d.get("problems", d) if isinstance(d, dict) else d)
            campaigns_panel()

        with step(7, "Final spec cards", "Once runs finish: each spec card with its result, saved under Cards."):
            if st.button("Collect final spec cards", key="b:final"):
                out = []
                for t, r, m, fl in combos[:MAX_PREVIEW]:
                    for sd in range(seeds):
                        s, b = api().send("POST", "/api/cards/spec/final", json={
                            "task": t, "recipe": r, "model": m, "provider": provider, "seed": sd, "fault": fl,
                            "prompts": False})
                        out.append((s, b, f"{t} x {r} x {m} seed {sd}{'' if fl == 'none' else ' ' + fl}"))
                st.session_state["b:finals"] = out
            for i, (s, b, label) in enumerate(st.session_state.get("b:finals") or []):
                if s != 200:
                    st.caption(f"{label}: {b.get('detail', b) if isinstance(b, dict) else b}")
                    continue
                res = b["result"]
                with st.expander(f"{res['status']} · E0 {res['E0']} · {label}"):
                    st.caption(f"saved {b['saved']}")
                    show_card(b, f"b:final:{i}")

    return {"Playbook": page_playbook, "What we can test with": page_options, "Cards": page_cards,
            "Build an experiment": page_build}
