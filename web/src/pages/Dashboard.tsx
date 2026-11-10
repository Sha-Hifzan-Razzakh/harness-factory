import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Entry, get, kindLabel } from "../api";
import { LineByDay, RankBars, Stat, StatusBars } from "../components/charts";
import { ActionBadge, ErrorNote, Loading, useLoad, when } from "../components/common";
import { ExperimentSummary, StateBadge } from "./Experiments";

interface Board { id: string; n: number; mean_e0: number | null; success: number | null; tokens: number | null; usd: number }
interface Dash {
  totals: { runs: number; distinct: number; scored: number; success: number | null; mean_e0: number | null; statuses: Record<string, number>;
    tokens: number; usd: number; recipes: number; models: number; tasks: number; experiments: number };
  timeline: { day: string; statuses: Record<string, number>; mean_e0: number | null }[];
  harnesses: Board[]; models: Board[];
  best_per_class: { task_class: string; recipe: string; model: string; mean_e0: number; n: number; candidates: number;
    runner_up: { recipe: string; model: string; mean_e0: number } | null }[];
  failing_checks: [string, number][]; violations: [string, number][];
  experiments: ExperimentSummary[]; changes: Entry[];
}

export default function Dashboard() {
  const [days, setDays] = useState(30);
  const d = useLoad(() => get<Dash>("/api/dashboard", { days }), [days]);
  const nav = useNavigate();
  if (d.error) return <ErrorNote error={d.error} />;
  if (!d.data) return <Loading what="dashboard" />;
  const x = d.data;
  const t = x.totals;
  const pct = (v: number | null) => (v === null ? "–" : `${Math.round(v * 100)}%`);
  return (
    <div className="stack">
      <div className="topbar">
        <div><h1>Dashboard</h1><div className="sub">Where the experiments stand. Scores are the latest scored clean run per exact experiment (RunKey).</div></div>
        <div className="row small">
          {[7, 30, 90].map((n) => <button key={n} className={`chip ${days === n ? "on" : ""}`} onClick={() => setDays(n)}>{n} days</button>)}
          <button onClick={d.reload}>Refresh</button>
        </div>
      </div>
      <div className="row" style={{ alignItems: "stretch" }}>
        <Stat label="Runs" value={t.runs.toLocaleString()} sub={`${t.distinct} distinct experiments`} />
        <Stat label="Mean E0" value={t.mean_e0 === null ? "–" : t.mean_e0.toFixed(3)} sub={`${t.scored} scored clean runs`} />
        <Stat label="SUCCESS" value={pct(t.success)} sub="of scored runs" />
        <Stat label="Not scored" value={String((t.statuses.INCOMPLETE ?? 0) + (t.statuses.ERROR ?? 0))}
              sub={`${t.statuses.INCOMPLETE ?? 0} INCOMPLETE · ${t.statuses.ERROR ?? 0} ERROR`} />
        <Stat label="Tokens · cost" value={t.tokens >= 1e6 ? `${(t.tokens / 1e6).toFixed(1)}M` : t.tokens.toLocaleString()} sub={`$${t.usd.toFixed(2)} at list prices`} />
        <Stat label="Coverage" value={`${t.recipes} × ${t.models}`} sub={`harnesses × models · ${t.tasks} tasks · ${t.experiments} experiments`} />
      </div>
      <div className="grid2">
        <StatusBars days={x.timeline} />
        <LineByDay label="Score trend" points={x.timeline.map((p) => ({ day: p.day, v: p.mean_e0 }))} />
      </div>
      <div className="grid2">
        <RankBars title="Harnesses by mean E0" sub="Across every task and model they ran on: compare like with like on the Experiments page."
          rows={x.harnesses.filter((h) => h.mean_e0 !== null).map((h) => ({ id: h.id, value: h.mean_e0!, note: `n=${h.n} · ${pct(h.success)} ok` }))}
          onPick={(id) => nav(`/workspace/recipe/${id}?tab=results`)} />
        <RankBars title="Models by mean E0" sub="Across every harness and task they ran with."
          rows={x.models.filter((h) => h.mean_e0 !== null).map((h) => ({ id: h.id, value: h.mean_e0!, note: `n=${h.n} · ${pct(h.success)} ok` }))} />
      </div>
      <div className="grid2">
        <div className="panel">
          <b>Best harness per task class</b>
          <div className="small muted">Highest mean E0 among the harness × model pairs that ran on that class. Few runs = provisional.</div>
          <table className="t" style={{ marginTop: 6 }}><thead><tr><th>Task class</th><th>Best</th><th>E0</th><th>n</th><th>Runner-up</th></tr></thead>
            <tbody>{x.best_per_class.map((b) => (
              <tr key={b.task_class}><td className="small">{b.task_class}</td>
                <td><b>{b.recipe}</b><div className="small muted">{b.model}</div></td><td>{b.mean_e0.toFixed(3)}</td>
                <td>{b.n}{b.n < 3 && <span className="badge warn" style={{ marginLeft: 4 }}>few</span>}</td>
                <td className="small">{b.runner_up ? <>{b.runner_up.recipe} · {b.runner_up.mean_e0.toFixed(3)}</> : <span className="muted">only one candidate</span>}</td></tr>
            ))}</tbody></table>
          {!x.best_per_class.length && <div className="muted small">No scored runs yet.</div>}
        </div>
        <div className="panel">
          <b>What fails most</b>
          <div className="small muted">Checks that failed in the latest scored runs that did not succeed.</div>
          <table className="t" style={{ marginTop: 6 }}><tbody>
            {x.failing_checks.map(([c, n]) => <tr key={c}><td className="small">{c}</td><td style={{ width: 60 }}><b>{n}</b></td></tr>)}
            {x.violations.map(([v, n]) => <tr key={v}><td className="small">violation: {v}</td><td><b>{n}</b></td></tr>)}
          </tbody></table>
          {!x.failing_checks.length && !x.violations.length && <div className="muted small">Nothing failing.</div>}
        </div>
      </div>
      <div className="grid2">
        <div className="panel">
          <div className="row"><b className="grow">Recent experiments</b><Link to="/experiments">all →</Link></div>
          {x.experiments.map((e) => (
            <div key={e.id} style={{ padding: "6px 0", borderBottom: "1px solid var(--line)" }}>
              <div className="row"><Link to={`/experiments/${e.id}`}><b>{e.name}</b></Link><StateBadge s={e.state} />
                <span className="grow" /><span className="small muted">{when(e.created_at)}</span></div>
              <div className="small muted">{e.done}/{e.planned} runs · mean E0 {e.stats.mean_e0?.toFixed(3) ?? "–"}{e.best && <> · best {e.best.recipe}</>}</div>
            </div>
          ))}
          {!x.experiments.length && <div className="muted small">No experiments yet. <Link to="/build">Build one →</Link></div>}
        </div>
        <div className="panel">
          <div className="row"><b className="grow">Recent catalog changes</b><Link to="/activity">all →</Link></div>
          {x.changes.map((e) => (
            <div key={e.seq} style={{ padding: "6px 0", borderBottom: "1px solid var(--line)" }}>
              <div className="row small"><ActionBadge action={e.action} /><Link to={`/workspace/${e.kind}/${e.id}?tab=history`}>{kindLabel(e.kind)} {e.id}</Link>
                {e.version !== null && <span className="badge accent">v{e.version}</span>}<span className="grow" /><span className="muted">{when(e.at)}</span></div>
              <div className="small muted">{e.note || "no reason given"} · {e.author}</div>
            </div>
          ))}
          {!x.changes.length && <div className="muted small">No changes recorded yet.</div>}
        </div>
      </div>
    </div>
  );
}
