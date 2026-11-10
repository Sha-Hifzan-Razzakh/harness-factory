import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { call, get } from "../api";
import { DeltaBars, Heatmap, LegendItem, STATUS_ORDER, STATUS_VAR } from "../components/charts";
import { ErrorNote, Loading, Modal, stamp, useLoad, when } from "../components/common";

interface Stats { n: number; scored: number; statuses: Record<string, number>; mean_e0: number | null; success: number | null; tokens: number | null; usd: number; wall_s: number }
interface Req { recipes: string[]; provider: string; models: string[] | null; tasks: string[] | null; seeds: number; faults: string[] | null; resume: boolean }
export interface ExperimentSummary {
  id: string; name: string; hypothesis: string; conclusion: string; tags: string[]; author: string; created_at: string;
  finished_at: string | null; state: string; error: string | null; request: Req; planned: number; done: number; stats: Stats;
  best: { recipe: string; mean_e0: number } | null; progress: { finished: number; of: number } | null;
}
interface Detail extends ExperimentSummary {
  per: ({ recipe: string; model: string; fault: string | null } & Stats)[];
  cells: ({ task: string; recipe: string; model: string; fault: string | null; run_ids: number[] } & Stats)[];
  compare: { base: string; recipe: string; model: string; n: number; mean_delta?: number; ci95?: [number, number]; wins?: number; losses?: number; ties?: number; verdict?: string }[];
  changed_since: { recipe: string; then: { hash: string; version: number | null }; now: { hash: string | null; version: number | null } }[];
  runs: { task_id: string; recipe_id: string; canonical_model: string; seed: number; fault: string | null; row: { id: number; status: string; composite: number } | null }[];
  plan: { total: number; will_run: number; done: number };
}

export function StateBadge({ s }: { s: string }) {
  const tone = { running: "accent", done: "good", failed: "bad", cancelled: "warn", interrupted: "warn" }[s] ?? "";
  return <span className={`badge ${tone}`}>{s}</span>;
}

export default function Experiments() {
  const { id } = useParams();
  return id ? <ExperimentDetail id={id} /> : <ExperimentList />;
}

function ExperimentList() {
  const list = useLoad(() => get<ExperimentSummary[]>("/api/experiments"), []);
  const [q, setQ] = useState("");
  const running = (list.data ?? []).some((e) => e.state === "running");
  useEffect(() => { if (!running) return; const t = setInterval(list.reload, 3000); return () => clearInterval(t); }, [running, list.reload]);
  const rows = (list.data ?? []).filter((e) => !q || `${e.name} ${e.hypothesis} ${e.conclusion} ${e.request.recipes.join(" ")}`.toLowerCase().includes(q.toLowerCase()));
  return (
    <div className="stack">
      <div className="topbar"><div><h1>Experiments</h1>
        <div className="sub">Every experiment: the question, its settings, how far it got and what it found. Saved, so it survives restarts.</div></div>
        <Link to="/build" className="btn">New experiment</Link></div>
      <input placeholder="filter by name, question, conclusion or harness" value={q} onChange={(e) => setQ(e.target.value)} />
      <ErrorNote error={list.error} />
      {list.loading && !list.data && <Loading />}
      {list.data && !rows.length && <div className="panel muted">No experiments yet. Build one: pick tasks, harnesses and a model, then Start.</div>}
      <div className="panel" style={{ padding: 0 }}>
        <table className="t">
          <thead><tr><th>Experiment</th><th>State</th><th>Progress</th><th>Harnesses</th><th>Mean E0</th><th>Best</th><th>When</th></tr></thead>
          <tbody>{rows.map((e) => (
            <tr key={e.id} className="click" onClick={() => (window.location.hash = `#/experiments/${e.id}`)}>
              <td><b>{e.name}</b><div className="small muted">{e.hypothesis || "no question written"}</div>
                {e.conclusion && <div className="small">→ {e.conclusion}</div>}</td>
              <td><StateBadge s={e.state} /></td>
              <td style={{ width: 150 }}><div className="bar"><i style={{ width: `${(100 * e.done) / Math.max(1, e.planned)}%` }} /></div>
                <span className="small muted">{e.done}/{e.planned} runs</span></td>
              <td className="small">{e.request.recipes.join(", ")}<div className="muted">{(e.request.models ?? []).join(", ")} @ {e.request.provider}</div></td>
              <td>{e.stats.mean_e0?.toFixed(3) ?? "–"}</td>
              <td className="small">{e.best ? <><b>{e.best.recipe}</b> {e.best.mean_e0.toFixed(3)}</> : "–"}</td>
              <td className="small muted" title={stamp(e.created_at)}>{when(e.created_at)}<div>{e.author}</div></td>
            </tr>))}</tbody>
        </table>
      </div>
    </div>
  );
}

function ExperimentDetail({ id }: { id: string }) {
  const d = useLoad(() => get<Detail>(`/api/experiments/${id}`), [id]);
  const nav = useNavigate();
  const [model, setModel] = useState<string | null>(null);
  const [notes, setNotes] = useState<{ name: string; hypothesis: string; conclusion: string } | null>(null);
  const [saved, setSaved] = useState(false);
  const [cell, setCell] = useState<{ task: string; recipe: string } | null>(null);
  const [rerun, setRerun] = useState<string | null>(null);
  const running = d.data?.state === "running";
  useEffect(() => { if (!running) return; const t = setInterval(d.reload, 2500); return () => clearInterval(t); }, [running, d.reload]);
  useEffect(() => { if (d.data && !notes) setNotes({ name: d.data.name, hypothesis: d.data.hypothesis, conclusion: d.data.conclusion }); }, [d.data, notes]);
  const x = d.data;
  const models = useMemo(() => [...new Set((x?.runs ?? []).map((r) => r.canonical_model))], [x]);
  const m = model ?? models[0];
  if (d.error) return <ErrorNote error={d.error} />;
  if (!x || !notes) return <Loading what="experiment" />;
  const tasks = [...new Set(x.runs.map((r) => r.task_id))];
  const recipes = x.request.recipes;
  const cellOf = (t: string, r: string) => x.cells.find((c) => c.task === t && c.recipe === r && c.model === m && !c.fault);
  const compare = x.compare.filter((c) => c.n > 0 && c.model === m);
  const saveNotes = async () => { await call("PATCH", `/api/experiments/${id}`, notes); setSaved(true); setTimeout(() => setSaved(false), 1500); d.reload(); };
  const startRerun = async (resume: boolean) => {
    const r = await call<{ id: string; detail?: unknown }>("POST", `/api/experiments/${id}/rerun`, { resume });
    if (r.status === 202) nav(`/experiments/${r.data.id}`);
    else setRerun(JSON.stringify((r.data as { detail?: unknown }).detail));
  };
  const buildLink = `/build?recipes=${recipes.join(",")}&tasks=${tasks.join(",")}&model=${x.request.provider}/${(x.request.models ?? [])[0] ?? ""}`;
  return (
    <div className="stack">
      <div className="topbar">
        <div><Link to="/experiments" className="small">← Experiments</Link><h1>{x.name} <StateBadge s={x.state} /></h1>
          <div className="sub">{x.author} · started {stamp(x.created_at)}{x.finished_at && <> · finished {stamp(x.finished_at)}</>}</div></div>
        <div className="row">
          {running && <button className="danger" onClick={async () => { await call("DELETE", `/api/experiments/${id}/run`); d.reload(); }}>Cancel</button>}
          <button onClick={() => startRerun(true)} title="Same settings with the catalog as it is now; runs already scored with the same identity are skipped">Run again</button>
          <Link to={buildLink} className="btn">Open in Build</Link>
        </div>
      </div>
      {rerun && <div className="note warn">{rerun} <button className="link" onClick={() => startRerun(false)}>Run them all again anyway</button></div>}
      {x.error && <div className="note warn">{x.error}</div>}
      {x.changed_since.length > 0 && (
        <div className="note warn">Changed since this experiment: {x.changed_since.map((c) => (
          <span key={c.recipe}> <Link to={`/workspace/recipe/${c.recipe}?tab=history`}><b>{c.recipe}</b></Link> (v{c.then.version ?? "?"} → v{c.now.version ?? "?"})</span>))}.
          Its results describe the old versions; <b>Run again</b> measures the current ones.</div>
      )}
      <div className="grid2">
        <div className="panel stack">
          <label className="field"><span>Name</span><input value={notes.name} onChange={(e) => setNotes({ ...notes, name: e.target.value })} /></label>
          <label className="field"><span>Question / hypothesis</span>
            <textarea rows={2} value={notes.hypothesis} onChange={(e) => setNotes({ ...notes, hypothesis: e.target.value })} /></label>
          <label className="field"><span>Conclusion (what you learned)</span>
            <textarea rows={3} value={notes.conclusion} placeholder="e.g. v2-recover ties v2-base on fs tasks; not worth its cost"
                      onChange={(e) => setNotes({ ...notes, conclusion: e.target.value })} /></label>
          <div className="row"><button className="primary" onClick={saveNotes}>Save notes</button>{saved && <span className="small muted">saved</span>}</div>
        </div>
        <div className="panel stack">
          <b>Progress</b>
          <div className="bar"><i style={{ width: `${(100 * x.done) / Math.max(1, x.planned)}%` }} /></div>
          <div className="row small">{x.done}/{x.planned} runs have a result
            {STATUS_ORDER.filter((s) => x.stats.statuses[s]).map((s) => <LegendItem key={s} color={STATUS_VAR[s]} label={`${s} ${x.stats.statuses[s]}`} />)}</div>
          <table className="t small"><tbody>
            <tr><td>Harnesses</td><td>{recipes.join(", ")} <span className="muted">(first = baseline)</span></td></tr>
            <tr><td>Models</td><td>{(x.request.models ?? []).join(", ")} @ {x.request.provider}</td></tr>
            <tr><td>Tasks</td><td>{tasks.length}: {tasks.slice(0, 12).join(", ")}{tasks.length > 12 ? " …" : ""}</td></tr>
            <tr><td>Seeds · faults</td><td>{x.request.seeds} · {(x.request.faults ?? ["none"]).join(", ")}</td></tr>
            <tr><td>Mean E0 · SUCCESS</td><td>{x.stats.mean_e0?.toFixed(3) ?? "–"} · {x.stats.success === null ? "–" : `${Math.round(x.stats.success * 100)}%`}</td></tr>
            <tr><td>Cost</td><td>${x.stats.usd.toFixed(4)} · {Math.round(x.stats.wall_s)} s of run time</td></tr>
          </tbody></table>
        </div>
      </div>
      {models.length > 1 && <div className="row">{models.map((mm) => <button key={mm} className={`chip ${mm === m ? "on" : ""}`} onClick={() => setModel(mm)}>{mm}</button>)}</div>}
      {compare.length > 0 && <DeltaBars rows={compare.map((c) => ({ label: `${c.recipe} vs ${c.base}`, mean: c.mean_delta!, lo: c.ci95![0], hi: c.ci95![1], n: c.n, verdict: c.verdict! }))} />}
      <Heatmap title={`Scores by task and harness · ${m}`} sub="Mean E0 over seeds, clean runs. Click a cell for its runs."
        rows={tasks} cols={recipes} onCell={(t, r) => setCell({ task: t, recipe: r })}
        value={(t, r) => { const c = cellOf(t, r); return c ? { v: c.mean_e0, label: `${c.n} run(s) · ${Object.entries(c.statuses).map(([k, v]) => `${k} ${v}`).join(", ")}` } : undefined; }} />
      <div className="panel">
        <b>Per harness × model</b>
        <table className="t" style={{ marginTop: 6 }}><thead><tr><th>Harness</th><th>Model</th><th>Fault</th><th>Runs</th><th>Mean E0</th><th></th><th>SUCCESS</th><th>Mean tokens</th><th>Cost</th></tr></thead>
          <tbody>{x.per.map((p, i) => (
            <tr key={i}><td><Link to={`/workspace/recipe/${p.recipe}`}><b>{p.recipe}</b></Link></td><td className="small">{p.model}</td><td className="small">{p.fault ?? "clean"}</td>
              <td>{p.n}</td><td>{p.mean_e0?.toFixed(3) ?? "–"}</td>
              <td style={{ width: 140 }}><div className="rank-bar"><i style={{ width: `${(p.mean_e0 ?? 0) * 100}%` }} /></div></td>
              <td>{p.success === null ? "–" : `${Math.round(p.success * 100)}%`}</td><td>{p.tokens?.toLocaleString() ?? "–"}</td><td>${p.usd.toFixed(4)}</td></tr>))}</tbody></table>
      </div>
      {cell && (
        <Modal title={`${cell.task} × ${cell.recipe} · ${m}`} onClose={() => setCell(null)}>
          <table className="t"><thead><tr><th>Seed</th><th>Fault</th><th>Status</th><th>E0</th><th></th></tr></thead>
            <tbody>{x.runs.filter((r) => r.task_id === cell.task && r.recipe_id === cell.recipe && r.canonical_model === m).map((r) => (
              <tr key={`${r.seed}-${r.fault}`}><td>{r.seed}</td><td>{r.fault ?? "clean"}</td><td>{r.row?.status ?? "not run yet"}</td>
                <td>{r.row?.composite.toFixed(3) ?? "–"}</td>
                <td>{r.row && <Link to={`/results?run=${r.row.id}`}>run {r.row.id} →</Link>}</td></tr>))}</tbody></table>
          <div className="small muted" style={{ marginTop: 8 }}>The final spec card of each run is under Cards → Final spec cards.</div>
        </Modal>
      )}
    </div>
  );
}
