import { useState } from "react";
import { Link } from "react-router-dom";
import { Card, enc, get } from "../api";
import { ErrorNote, Loading, Markdown, useLoad } from "../components/common";

const TABS: [string, string, string][] = [
  ["shapes", "Shapes", "The control flow around the model: the main thing a harness varies."],
  ["recipe_fields", "Recipe fields", "Every field a recipe can set, its default and allowed values: the full menu of dimensions."],
  ["recipes", "Harnesses", "Base recipes, and matrix recipes = a base plus one change."],
  ["tasks", "Tasks", "What harnesses are measured on; the class decides which cell a result lands in."],
  ["models", "Models", "Canonical models per provider."],
  ["tools", "Tools", "Every declared tool by server, with side effect and risk."],
  ["skills", "Skills", "General how-tos a recipe can give the model."],
  ["prompts", "Prompts", "Templates by layer, and which recipes use them."],
  ["faults", "Fault profiles", "Tools that fail on purpose, to measure robustness."],
  ["runtimes", "Runtimes", "native = the factory's shapes; others run a framework's own agent (tool-loop recipes)."],
  ["check_kinds", "Check kinds", "How a task's oracle grades the end state."],
  ["perturbations", "Perturbations", "Ways a task variant is changed (hfx perturb)."],
  ["retrieval_modes", "Retrieval modes", "retrieval.mode for the retrieve__search tool."],
];
const WORKSPACE: Record<string, [string, string]> = { recipes: ["recipe", "id"], tasks: ["task", "id"], skills: ["skill", "id"],
  prompts: ["prompt", "template"], faults: ["fault", "id"] };

export default function Reference() {
  const opts = useLoad(() => get<Record<string, Record<string, unknown>[]>>("/api/options"), []);
  const [tab, setTab] = useState("shapes");
  const [q, setQ] = useState("");
  if (opts.error) return <ErrorNote error={opts.error} />;
  if (!opts.data) return <Loading what="options" />;
  const rows = (opts.data[tab] ?? []).filter((r) => !q || JSON.stringify(r).toLowerCase().includes(q.toLowerCase()));
  const cols = Object.keys(rows[0] ?? opts.data[tab]?.[0] ?? {});
  const link = WORKSPACE[tab];
  return (
    <div>
      <div className="topbar"><div><h1>What we can test with</h1><div className="sub">Read live from the catalog: every building block and every dimension.</div></div></div>
      <div className="tabs">{TABS.map(([k, label]) => <button key={k} className={tab === k ? "on" : ""} onClick={() => { setTab(k); setQ(""); }}>
        {label} <span className="muted small">{opts.data![k]?.length}</span></button>)}</div>
      <div className="row" style={{ marginBottom: 8 }}><span className="grow muted">{TABS.find((t) => t[0] === tab)?.[2]}</span>
        <input placeholder="filter" value={q} onChange={(e) => setQ(e.target.value)} /></div>
      <div className="panel" style={{ overflow: "auto" }}>
        <table className="t"><thead><tr>{cols.map((c) => <th key={c}>{c}</th>)}{link && <th />}</tr></thead>
          <tbody>{rows.map((r, i) => (
            <tr key={i}>{cols.map((c) => <td key={c} className={c === "instruction" || c === "meaning" ? "small" : ""}>{String(r[c] ?? "")}</td>)}
              {link && <td><Link to={`/workspace/${link[0]}/${String(r[link[1]])}`}>open</Link></td>}</tr>
          ))}</tbody></table>
      </div>
    </div>
  );
}

export function Cards() {
  const opts = useLoad(() => get<Record<string, Record<string, string>[]>>("/api/options"), []);
  const saved = useLoad(() => get<{ name: string; title: string; summary: string; status: string | null; E0: number | null }[]>("/api/cards/saved"), []);
  const [kind, setKind] = useState("harness");
  const [id, setId] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const card = useLoad(() => (id ? get<Card>(`/api/cards/${kind}/${enc(id)}`) : Promise.resolve(null)), [kind, id]);
  const savedCard = useLoad(() => (open ? get<Card>(`/api/cards/saved/${open}`) : Promise.resolve(null)), [open]);
  if (!opts.data) return <Loading />;
  const ids: Record<string, string[]> = {
    harness: opts.data.recipes.map((r) => r.id), model: [...new Set(opts.data.models.map((m) => m.model))],
    task: opts.data.tasks.map((t) => t.id), tool: [...new Set(opts.data.tools.map((t) => t.server))], fault: opts.data.faults.map((f) => f.id),
  };
  return (
    <div>
      <div className="topbar"><div><h1>Cards</h1><div className="sub">The spec of each building block, and the final spec cards of experiments that ran (their evidence).</div></div></div>
      <div className="split">
        <div className="panel stack">
          <h3 style={{ marginTop: 0 }}>Final spec cards</h3>
          {saved.loading ? <Loading /> : !(saved.data ?? []).length ? <div className="muted small">None yet: Build → run → the grid saves them.</div> : (
            <div className="list" style={{ maxHeight: 260 }}>{(saved.data ?? []).map((s) => (
              <div key={s.name} className={`it ${open === s.name ? "on" : ""}`} onClick={() => { setOpen(s.name); setId(""); }}>
                <div className="id">{s.title.replace("Spec card: ", "")}</div>
                <div className="meta">{s.status} · E0 {s.E0 ?? "–"} · {s.name.split("-").slice(1).join("-")}</div></div>))}</div>)}
          <h3>Building blocks</h3>
          <select value={kind} onChange={(e) => { setKind(e.target.value); setId(""); }}>
            {Object.keys(ids).map((k) => <option key={k}>{k}</option>)}</select>
          <div className="list" style={{ maxHeight: 300 }}>{ids[kind].map((x) => (
            <div key={x} className={`it ${id === x ? "on" : ""}`} onClick={() => { setId(x); setOpen(null); }}>{x}</div>))}</div>
        </div>
        <div className="panel">
          {open ? (savedCard.data ? <CardBody c={savedCard.data} /> : <Loading />)
            : id ? (card.error ? <ErrorNote error={card.error} /> : card.data ? <CardBody c={card.data} /> : <Loading />)
            : <div className="muted">Pick a card.</div>}
        </div>
      </div>
    </div>
  );
}

function CardBody({ c }: { c: Card }) {
  const download = (name: string, text: string) => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
    a.download = name;
    a.click();
  };
  return (
    <div className="stack">
      <div className="row"><h2 className="grow" style={{ margin: 0 }}>{c.title}</h2>
        <button onClick={() => download(`${c.kind}-${c.id}.md`, c.markdown)}>.md</button>
        <button onClick={() => { const { markdown, ...rest } = c; void markdown; download(`${c.kind}-${c.id}.json`, JSON.stringify(rest, null, 2)); }}>.json</button></div>
      {c.verdict && <div className={`note ${c.verdict === "READY" ? "good" : "bad"}`}><b>{c.verdict}</b> · {c.summary}</div>}
      <Markdown text={c.markdown} />
    </div>
  );
}
