import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { call, Card, get } from "../api";
import { ErrorNote, Loading, Markdown, Modal, useLoad } from "../components/common";

interface Options {
  tasks: { id: string; class: string; domain: string; split: string; checks: number; tags: string; instruction: string }[];
  recipes: { id: string; kind: string; base: string; shape: string; changes: string }[];
  models: { provider: string; model: string; "key set": boolean }[];
  faults: { id: string; description: string }[];
}
interface Campaign { id: string; state: string; plan: { will_run: number }; progress: { finished: number; of: number; statuses: Record<string, number> }; error?: string | null }
type CellState = { card?: Card; loading?: boolean; error?: string; final?: Card };

const MAX_CELLS = 150;

export default function Build() {
  const [sp] = useSearchParams();
  const opts = useLoad(() => get<Options>("/api/options"), []);
  const nav = useNavigate();
  const [tasks, setTasks] = useState<string[]>(sp.get("tasks")?.split(",").filter(Boolean) ?? ["fs-02"]);
  const [naming, setNaming] = useState(false);
  const [expName, setExpName] = useState("");
  const [hypothesis, setHypothesis] = useState("");
  const [recipes, setRecipes] = useState<string[]>(sp.get("recipes")?.split(",").filter(Boolean) ?? ["v2-base"]);
  const presetModel = sp.get("model");
  const [provider, setProvider] = useState(presetModel?.split("/")[0] ?? "scripted");
  const [models, setModels] = useState<string[]>(presetModel ? [presetModel.split("/").slice(1).join("/")] : ["scripted/replay"]);
  const [seeds, setSeeds] = useState(1);
  const [faults, setFaults] = useState<string[]>([]);
  const [taskQ, setTaskQ] = useState("");
  const [cls, setCls] = useState("");
  const [cells, setCells] = useState<Record<string, CellState>>({});
  const [open, setOpen] = useState<{ key: string; card?: Card } | null>(null);
  const [plan, setPlan] = useState<{ status: number; data: Record<string, unknown> } | null>(null);
  const [campaign, setCampaign] = useState<Campaign | null>(null);
  const [startError, setStartError] = useState<string | null>(null);
  const gen = useRef(0);

  const o = opts.data;
  const providers = useMemo(() => [...new Set((o?.models ?? []).map((m) => m.provider))], [o]);
  const served = (o?.models ?? []).filter((m) => m.provider === provider);
  const classes = [...new Set((o?.tasks ?? []).map((t) => t.class))].sort();
  const pool = (o?.tasks ?? []).filter((t) => t.split !== "HELD_OUT" && (!cls || t.class === cls)
    && (!taskQ || `${t.id} ${t.tags} ${t.instruction}`.toLowerCase().includes(taskQ.toLowerCase())));
  const combos = useMemo(() => tasks.flatMap((t) => recipes.flatMap((r) => models.map((m) => ({ t, r, m })))), [tasks, recipes, models]);
  const key = (t: string, r: string, m: string) => `${t}|${r}|${m}`;

  // Spec cards for every cell, a few at a time, refreshed whenever the selection changes.
  useEffect(() => {
    const my = ++gen.current;
    setCells({});
    setPlan(null);
    const todo = combos.slice(0, MAX_CELLS);
    let i = 0;
    const worker = async () => {
      while (i < todo.length && gen.current === my) {
        const { t, r, m } = todo[i++];
        const k = key(t, r, m);
        setCells((c) => ({ ...c, [k]: { loading: true } }));
        const res = await call<Card>("POST", "/api/cards/spec", { task: t, recipe: r, model: m, provider, fault: faults[0] ?? null, prompts: false });
        if (gen.current !== my) return;
        setCells((c) => ({ ...c, [k]: res.status === 200 ? { card: res.data } : { error: JSON.stringify(res.data) } }));
      }
    };
    const timer = setTimeout(() => { for (let w = 0; w < 4; w++) worker(); }, 300);
    return () => clearTimeout(timer);
  }, [combos, provider, faults]);

  // Campaign progress, then the final spec cards.
  useEffect(() => {
    if (!campaign || campaign.state !== "running") return;
    const t = setInterval(async () => {
      const c = await get<Campaign>(`/api/campaigns/${campaign.id}`);
      setCampaign(c);
      if (c.state !== "running") collectFinals();
    }, 2000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [campaign?.id, campaign?.state]);

  async function collectFinals() {
    for (const { t, r, m } of combos.slice(0, MAX_CELLS)) {
      const k = key(t, r, m);
      if (cells[k]?.card?.verdict !== "READY") continue;
      const res = await call<Card>("POST", "/api/cards/spec/final", { task: t, recipe: r, model: m, provider, seed: 0, fault: faults[0] ?? null, prompts: false });
      if (res.status === 200) setCells((c) => ({ ...c, [k]: { ...c[k], final: res.data } }));
    }
  }

  const request = () => ({ recipes, provider, models, tasks, seeds, concurrency: 4, faults: faults.length ? ["none", ...faults] : null, resume: true });
  const all = Object.values(cells);
  const ready = all.filter((c) => c.card?.verdict === "READY").length;
  const unreachable = all.filter((c) => c.card?.verdict === "BLOCKED" && c.card.issues.every((i) => i.includes("can't call"))).length;
  const blocked = all.filter((c) => c.card?.verdict === "BLOCKED").length - unreachable;

  if (opts.error) return <ErrorNote error={opts.error} />;
  if (!o) return <Loading what="options" />;
  return (
    <div>
      <div className="topbar"><div><h1>Build an experiment</h1>
        <div className="sub">Tasks × harnesses × models. Every cell is a spec card: what that run would use, and whether it can run. Nothing runs until Start.</div></div></div>
      <div className="split">
        <div className="panel stack">
          <h3 style={{ marginTop: 0 }}>1 · Tasks ({tasks.length})</h3>
          <div className="row"><input className="grow" placeholder="search id, tag, instruction" value={taskQ} onChange={(e) => setTaskQ(e.target.value)} />
            <select value={cls} onChange={(e) => setCls(e.target.value)}><option value="">any class</option>{classes.map((c) => <option key={c}>{c}</option>)}</select></div>
          <div className="row small"><button className="link" onClick={() => setTasks([...new Set([...tasks, ...pool.map((t) => t.id)])])}>add all {pool.length} shown</button>
            <button className="link" onClick={() => setTasks([])}>clear</button></div>
          <div className="list" style={{ maxHeight: 220 }}>
            {pool.map((t) => (
              <label key={t.id} className="row small" title={t.instruction}>
                <input type="checkbox" checked={tasks.includes(t.id)} onChange={(e) => setTasks(e.target.checked ? [...tasks, t.id] : tasks.filter((x) => x !== t.id))} />
                <b>{t.id}</b><span className="muted">{t.class}</span></label>
            ))}
          </div>
          <h3>2 · Harnesses ({recipes.length})</h3>
          <div className="list" style={{ maxHeight: 200 }}>
            {o.recipes.map((r) => (
              <label key={r.id} className="row small" title={r.changes || r.shape}>
                <input type="checkbox" checked={recipes.includes(r.id)} onChange={(e) => setRecipes(e.target.checked ? [...recipes, r.id] : recipes.filter((x) => x !== r.id))} />
                <b>{r.id}</b><span className="muted">{r.kind === "matrix" ? `${r.base} + ${r.changes}` : r.shape}</span></label>
            ))}
          </div>
          <h3>3 · Model</h3>
          <select value={provider} onChange={(e) => { setProvider(e.target.value); setModels([]); }}>{providers.map((p) => <option key={p}>{p}</option>)}</select>
          {served.length > 0 && !served[0]["key set"] && <div className="note warn small">The API key for {provider} is not set: runs would end ERROR.</div>}
          {served.map((m) => (
            <label key={m.model} className="row small"><input type="checkbox" checked={models.includes(m.model)}
              onChange={(e) => setModels(e.target.checked ? [...models, m.model] : models.filter((x) => x !== m.model))} />{m.model}</label>
          ))}
          <h3>4 · Settings</h3>
          <div className="row small"><label>Seeds <input type="number" min={1} max={20} value={seeds} onChange={(e) => setSeeds(Number(e.target.value))} style={{ width: 60 }} /></label></div>
          <div className="small muted">Fault profile (runs it next to clean runs, for R):</div>
          <select value={faults[0] ?? ""} onChange={(e) => setFaults(e.target.value ? [e.target.value] : [])}>
            <option value="">none (clean runs only)</option>{o.faults.map((f) => <option key={f.id} value={f.id}>{f.id}: {f.description}</option>)}</select>
        </div>
        <div className="stack">
          <div className="panel">
            <div className="row"><h3 className="grow" style={{ margin: 0 }}>5 · Spec cards</h3>
              <span className="badge good">{ready} ready</span>{blocked > 0 && <span className="badge bad">{blocked} blocked</span>}
              {unreachable > 0 && <span className="badge">{unreachable} can't reach the task's tools (skipped)</span>}</div>
            {combos.length > MAX_CELLS && <div className="note warn small">{combos.length} cells: only the first {MAX_CELLS} are previewed.</div>}
            {models.map((m) => (
              <div key={m} style={{ overflow: "auto", marginTop: 10 }}>
                <div className="small muted">{m} @ {provider}</div>
                <table className="grid-matrix">
                  <thead><tr><th className="r">task \ harness</th>{recipes.map((r) => <th key={r}>{r}</th>)}</tr></thead>
                  <tbody>{tasks.map((t) => (
                    <tr key={t}><th className="r">{t}</th>{recipes.map((r) => {
                      const c = cells[key(t, r, m)];
                      const v = c?.card?.verdict;
                      const skip = v === "BLOCKED" && c?.card?.issues.every((i) => i.includes("can't call"));
                      const res = c?.final?.result as { status?: string; E0?: number } | undefined;
                      return (
                        <td key={r}><div className={`cell ${skip ? "skip" : v === "READY" ? "ready" : v === "BLOCKED" ? "blocked" : ""}`}
                          onClick={() => setOpen({ key: key(t, r, m), card: c?.final ?? c?.card })}>
                          {c?.loading ? "…" : c?.error ? "error" : res ? `${res.status} ${res.E0?.toFixed(2)}` : skip ? "skipped" : v ?? ""}
                        </div></td>
                      );
                    })}</tr>
                  ))}</tbody>
                </table>
              </div>
            ))}
            <div className="small muted" style={{ marginTop: 8 }}>Click a cell for its full spec card: RunKey, the tools the model will see, budgets, issues, cost estimate.</div>
          </div>
          <div className="panel stack">
            <h3 style={{ margin: 0 }}>6 · Run</h3>
            <div className="row">
              <button onClick={async () => setPlan(await call("POST", "/api/campaigns/plan", request()))} disabled={!ready}>Plan</button>
              <button className="primary" disabled={!ready || blocked > 0 || campaign?.state === "running"} onClick={() => {
                setStartError(null);
                if (!expName) setExpName(recipes.length > 1 ? `${recipes.slice(1).join(", ")} vs ${recipes[0]}` : `${recipes[0]} on ${tasks.length} task(s)`);
                setNaming(true);
              }}>Start experiment {seeds > 1 ? `(${seeds} seeds)` : ""}</button>
              <button disabled={!ready || blocked > 0 || campaign?.state === "running"} title="Run without saving it as an experiment" onClick={async () => {
                setStartError(null);
                const r = await call<Campaign>("POST", "/api/campaigns", request());
                if (r.status === 202) setCampaign(r.data);
                else setStartError(JSON.stringify((r.data as unknown as { detail: unknown }).detail));
              }}>Quick run</button>
              {blocked > 0 && <span className="small" style={{ color: "var(--bad)" }}>Fix the blocked cells first (open one to see why).</span>}
            </div>
            {plan && (plan.status === 200 ? (
              <div className="small">
                <div><b>{String(plan.data.total)}</b> runs planned: {String(plan.data.done)} already scored, {String(plan.data.retry)} to retry, {String(plan.data.new)} new → <b>{String(plan.data.will_run)}</b> will run</div>
                <table className="t"><thead><tr>{Object.keys((plan.data.projection as Record<string, unknown>[])[0] ?? {}).map((h) => <th key={h}>{h}</th>)}</tr></thead>
                  <tbody>{(plan.data.projection as Record<string, unknown>[]).map((p, i) => <tr key={i}>{Object.values(p).map((v, j) => <td key={j}>{typeof v === "object" ? JSON.stringify(v) : String(v)}</td>)}</tr>)}</tbody></table>
                {(plan.data.problems as string[]).map((p) => <div key={p} className="note bad">Would be refused: {p}</div>)}
              </div>) : <ErrorNote error={JSON.stringify(plan.data)} />)}
            {startError && <ErrorNote error={startError} />}
            {campaign && (
              <div>
                <div className="row small"><b>Campaign {campaign.id}</b> <span className="badge accent">{campaign.state}</span>
                  {Object.entries(campaign.progress?.statuses ?? {}).map(([s, n]) => <span key={s} className="badge">{s} {n}</span>)}</div>
                <div className="bar" style={{ marginTop: 6 }}><i style={{ width: `${100 * (campaign.progress?.finished ?? 0) / Math.max(1, campaign.progress?.of ?? 1)}%` }} /></div>
                {campaign.state !== "running" && <button style={{ marginTop: 8 }} onClick={collectFinals}>Refresh results in the grid</button>}
              </div>
            )}
          </div>
        </div>
      </div>
      {naming && (
        <Modal title="Start the experiment" onClose={() => setNaming(false)}>
          <div className="stack">
            <label className="field"><span>Name</span><input value={expName} onChange={(e) => setExpName(e.target.value)} /></label>
            <label className="field"><span>Question / hypothesis: what should this tell you?</span>
              <textarea rows={3} value={hypothesis} placeholder="e.g. Does re-asking after a malformed reply help on fs tasks, and at what cost?"
                        onChange={(e) => setHypothesis(e.target.value)} /></label>
            <div className="small muted">{combos.length * seeds} planned runs · {recipes[0]} is the baseline the others are compared against.</div>
            <div className="row"><span className="grow" /><button onClick={() => setNaming(false)}>Cancel</button>
              <button className="primary" disabled={!expName.trim()} onClick={async () => {
                const r = await call<{ id: string }>("POST", "/api/experiments", { name: expName.trim(), hypothesis, request: request() });
                setNaming(false);
                if (r.status === 202) nav(`/experiments/${r.data.id}`);
                else setStartError(JSON.stringify((r.data as unknown as { detail: unknown }).detail));
              }}>Start</button></div>
          </div>
        </Modal>
      )}
      {open && (
        <Modal title={open.card?.title ?? open.key} onClose={() => setOpen(null)}>
          <SpecCard k={open.key} provider={provider} fault={faults[0] ?? null} fallback={open.card} />
        </Modal>
      )}
    </div>
  );
}

function SpecCard({ k, provider, fault, fallback }: { k: string; provider: string; fault: string | null; fallback?: Card }) {
  const [t, r, m] = k.split("|");
  const card = useLoad(async () => (await call<Card>("POST", "/api/cards/spec", { task: t, recipe: r, model: m, provider, fault, prompts: true })).data, [k]);
  const c = fallback?.result ? fallback : card.data ?? fallback;
  if (!c) return <Loading what="spec card" />;
  return (
    <div className="stack">
      <div className={`note ${c.verdict === "READY" ? "good" : "bad"}`}><b>{c.verdict}</b> · {c.summary}</div>
      {c.issues.map((i) => <div key={i} className="note warn small">{i}</div>)}
      <Markdown text={c.markdown} />
    </div>
  );
}
