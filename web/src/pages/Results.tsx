import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { get } from "../api";
import { ErrorNote, Loading, Markdown, Modal, useLoad } from "../components/common";

interface Cell {
  task_class: string; recipe_id: string; recipe_hash: string; canonical_model: string; provider: string;
  e: { n: number; mean: number; ci95: [number, number]; pass_rate: number }; mean_e0: number; mean_c: number;
  robustness: number | null; excluded: number; tokens: number; usd: number;
}
interface Run { id: number; task_version: string; recipe_id: string; canonical_model: string; provider: string; status: string;
  composite: number; tool_calls: number; model_calls: number; tokens_in: number; tokens_out: number; fault: string | null; created_at: string }
interface Msg { role: string; content?: string; tool_calls?: unknown[]; name?: string }

export default function Results() {
  const cells = useLoad(() => get<{ cells: Cell[] }>("/api/results/cells"), []);
  const runs = useLoad(() => get<Run[]>("/api/results", { limit: 300 }), []);
  const [cls, setCls] = useState("");
  const [model, setModel] = useState("");
  const [transcript, setTranscript] = useState<{ id: number; data?: { status?: string; output?: string; messages?: Msg[] }; error?: string } | null>(null);
  const [report, setReport] = useState<string | null>(null);
  const [sp] = useSearchParams();
  useEffect(() => { const r = Number(sp.get("run")); if (r) openTranscript(r); }, [sp]);  // eslint-disable-line react-hooks/exhaustive-deps

  const rows = useMemo(() => (cells.data?.cells ?? []).filter((c) => (!cls || c.task_class === cls) && (!model || c.canonical_model === model))
    .sort((a, b) => a.task_class.localeCompare(b.task_class) || b.e.mean - a.e.mean), [cells.data, cls, model]);
  const classes = [...new Set((cells.data?.cells ?? []).map((c) => c.task_class))].sort();
  const models = [...new Set((cells.data?.cells ?? []).map((c) => c.canonical_model))].sort();
  const bestPerClass = useMemo(() => {
    const b: Record<string, number> = {};
    for (const c of rows) b[`${c.task_class}|${c.canonical_model}`] = Math.max(b[`${c.task_class}|${c.canonical_model}`] ?? 0, c.e.mean);
    return b;
  }, [rows]);

  async function openTranscript(id: number) {
    setTranscript({ id });
    try {
      setTranscript({ id, data: await get(`/api/runs/${id}/transcript`) });
    } catch (e) {
      setTranscript({ id, error: String(e) });
    }
  }

  return (
    <div>
      <div className="topbar"><div><h1>Results</h1>
        <div className="sub">Cells = task class × harness × model. E is cost-adjusted (E0 − w·C); the bar shows its 95% interval. Overlapping intervals are a close call.</div></div>
        <button onClick={async () => setReport((await get<{ markdown: string }>("/api/report")).markdown)}>Campaign report</button></div>
      <div className="panel row" style={{ marginBottom: 12 }}>
        <select value={cls} onChange={(e) => setCls(e.target.value)}><option value="">all task classes</option>{classes.map((c) => <option key={c}>{c}</option>)}</select>
        <select value={model} onChange={(e) => setModel(e.target.value)}><option value="">all models</option>{models.map((m) => <option key={m}>{m}</option>)}</select>
      </div>
      <ErrorNote error={cells.error} />
      {cells.loading ? <Loading /> : !rows.length ? <div className="panel muted">No scored runs yet. Build and run an experiment.</div> : (
        <div className="panel" style={{ overflow: "auto" }}>
          <table className="t">
            <thead><tr><th>Task class</th><th>Harness</th><th>Model</th><th>n</th><th>E</th><th style={{ width: 160 }}>95% interval</th><th>pass</th><th>E0</th><th>C</th><th>R</th><th>excl.</th><th>tokens</th></tr></thead>
            <tbody>{rows.map((c, i) => (
              <tr key={i}>
                <td className="small">{c.task_class}</td>
                <td><b>{c.recipe_id}</b> {c.e.mean === bestPerClass[`${c.task_class}|${c.canonical_model}`] && <span className="badge good">top</span>}</td>
                <td className="small">{c.canonical_model}<div className="muted">{c.provider}</div></td>
                <td>{c.e.n}</td><td><b>{c.e.mean.toFixed(3)}</b></td>
                <td><div className="bar" style={{ position: "relative" }}>
                  <i style={{ marginLeft: `${c.e.ci95[0] * 100}%`, width: `${Math.max(1, (c.e.ci95[1] - c.e.ci95[0]) * 100)}%` }} /></div>
                  <span className="small muted">{c.e.ci95[0].toFixed(2)}–{c.e.ci95[1].toFixed(2)}</span></td>
                <td>{Math.round(c.e.pass_rate * 100)}%</td><td>{c.mean_e0.toFixed(3)}</td><td>{c.mean_c.toFixed(2)}</td>
                <td>{c.robustness === null ? "–" : c.robustness.toFixed(2)}</td><td>{c.excluded}</td><td>{c.tokens.toLocaleString()}</td>
              </tr>))}</tbody>
          </table>
        </div>
      )}
      <h2>Recent runs</h2>
      <div className="panel" style={{ overflow: "auto" }}>
        {runs.loading ? <Loading /> : (
          <table className="t">
            <thead><tr><th>#</th><th>Task</th><th>Harness</th><th>Model</th><th>Fault</th><th>Status</th><th>E0</th><th>calls</th><th>tokens</th><th>when</th></tr></thead>
            <tbody>{(runs.data ?? []).map((r) => (
              <tr key={r.id} className="click" onClick={() => openTranscript(r.id)}>
                <td>{r.id}</td><td>{r.task_version}</td><td>{r.recipe_id}</td><td className="small">{r.canonical_model}</td><td>{r.fault ?? ""}</td>
                <td><span className={`badge ${r.status === "SUCCESS" ? "good" : r.status === "FAIL" || r.status === "ERROR" ? "bad" : "warn"}`}>{r.status}</span></td>
                <td>{r.composite.toFixed(3)}</td><td className="small">{r.model_calls}/{r.tool_calls}</td><td>{(r.tokens_in + r.tokens_out).toLocaleString()}</td>
                <td className="small muted">{String(r.created_at).slice(0, 19).replace("T", " ")}</td></tr>))}</tbody>
          </table>)}
      </div>
      {transcript && (
        <Modal title={`Run ${transcript.id}`} onClose={() => setTranscript(null)}>
          {transcript.error ? <ErrorNote error={transcript.error} /> : !transcript.data ? <Loading what="transcript" /> : (
            <div className="stack">
              <div className="note"><b>{transcript.data.status}</b> · answer: {transcript.data.output ?? "—"}</div>
              {(transcript.data.messages ?? []).map((m, i) => (
                <div key={i} className="panel">
                  <div className="small muted">{m.role}{m.name ? ` · ${m.name}` : ""}</div>
                  {m.content && <pre>{typeof m.content === "string" ? m.content : JSON.stringify(m.content, null, 1)}</pre>}
                  {(m.tool_calls ?? []).map((c, j) => <pre key={j} className="small" style={{ color: "var(--accent)" }}>{JSON.stringify(c, null, 1)}</pre>)}
                </div>))}
            </div>)}
        </Modal>
      )}
      {report !== null && <Modal title="Campaign report" onClose={() => setReport(null)}><Markdown text={report} /></Modal>}
    </div>
  );
}
