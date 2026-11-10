import { useState } from "react";
import { Link } from "react-router-dom";
import { call, ChangeResult, enc, get, History, Kind, VersionStats } from "../api";
import { ChangeFlow } from "./ChangeFlow";
import { ActionBadge, Diff, ErrorNote, Loading, Modal, stamp, useLoad, when } from "./common";

const TAGS = ["baseline", "best"];

export function HistoryView({ kind, id, onChanged }: { kind: Kind; id: string; onChanged: () => void }) {
  const h = useLoad(() => get<History>(`/api/history/${kind}/${enc(id)}`), [kind, id]);
  const [pick, setPick] = useState<number[]>([]);
  const [compare, setCompare] = useState<{ a: number | string; b: number | string; text: string } | null>(null);
  const [open, setOpen] = useState<Record<number, string>>({});
  const [restoring, setRestoring] = useState<number | null>(null);
  const [viewing, setViewing] = useState<{ v: number; parts: Record<string, string> } | null>(null);
  const [syncing, setSyncing] = useState(false);

  if (h.error) return <ErrorNote error={h.error} />;
  if (!h.data) return <Loading what="history" />;
  const hist = h.data;

  async function showDiff(a: number | string, b: number | string) {
    const d = await get<{ diff: string }>(`/api/history/${kind}/${enc(id)}/diff`, { a: String(a), b: String(b) });
    return d.diff;
  }
  async function toggleDiff(v: number) {
    if (open[v] !== undefined) { const o = { ...open }; delete o[v]; setOpen(o); return; }
    setOpen({ ...open, [v]: v > 1 ? await showDiff(v - 1, v) : "(the first version)" });
  }
  async function tag(v: number, t: string, on: boolean) {
    await call("POST", `/api/history/${kind}/${enc(id)}/tags`, { version: v, tags: [on ? t : `-${t}`] });
    h.reload();
  }
  async function sync() {
    setSyncing(true);
    await call("POST", "/api/sync");
    setSyncing(false);
    h.reload();
    onChanged();
  }

  return (
    <div className="stack">
      {hist.changed_outside && (
        <div className="note warn row"><span className="grow">The files changed outside the workspace (editor, git) since the last version.</span>
          <button onClick={sync} disabled={syncing}>{syncing ? "Recording…" : "Record it as a version"}</button></div>
      )}
      {hist.versions.length === 0 && (
        <div className="note row"><span className="grow">No history yet. Sync records the current state as v1.</span>
          <button onClick={sync} disabled={syncing}>Start history</button></div>
      )}
      {hist.versions.length > 1 && (
        <div className="row small">
          <span className="muted">Pick two versions to compare:</span>
          <button disabled={pick.length !== 2} onClick={async () => {
            const [a, b] = [...pick].sort((x, y) => x - y);
            setCompare({ a, b, text: await showDiff(a, b) });
          }}>Compare {pick.length === 2 ? `v${Math.min(...pick)} → v${Math.max(...pick)}` : ""}</button>
          {hist.live && <button onClick={async () => setCompare({ a: hist.versions[hist.versions.length - 1].version!, b: "current",
            text: await showDiff(hist.versions[hist.versions.length - 1].version!, "current") })}>First version → now</button>}
        </div>
      )}
      <div className="timeline">
        {hist.versions.map((v) => {
          const n = v.version!;
          const changed = v.impact?.recipes_changed ?? [];
          return (
            <div key={v.seq} className={`v ${v.action === "dependency" ? "dep" : v.action === "archive" ? "arch" : ""}`}>
              <div className="row">
                <input type="checkbox" checked={pick.includes(n)}
                       onChange={(e) => setPick(e.target.checked ? [...pick, n].slice(-2) : pick.filter((x) => x !== n))} />
                <b>v{n}</b> <ActionBadge action={v.action} />
                {v.tags.map((t) => <span key={t} className="badge good">{t}</span>)}
                <span className="muted small" title={stamp(v.at)}>{when(v.at)} · {v.author}</span>
                <span className="grow" />
                <code className="small muted">{v.hash_after ?? ""}</code>
              </div>
              <div style={{ margin: "3px 0 3px 24px" }}>
                {v.note ? <span>{v.note}</span> : <span className="muted">no reason given</span>}
                {v.cause && <span className="muted"> · because of {v.cause}</span>}
                {changed.length > 0 && <div className="small muted">re-hashed: {changed.join(", ")}</div>}
              </div>
              <div className="row small" style={{ marginLeft: 24 }}>
                <button className="link" onClick={() => toggleDiff(n)}>{open[n] !== undefined ? "Hide changes" : "What changed"}</button>
                <button className="link" onClick={async () => setViewing({ v: n, parts: (await get<{ parts: Record<string, string> }>(
                  `/api/history/${kind}/${enc(id)}/version/${n}`)).parts })}>Files</button>
                {kind !== "task" && v.action !== "archive" && <button className="link" onClick={() => setRestoring(n)}>Restore this version</button>}
                {TAGS.map((t) => (
                  <button key={t} className="link" onClick={() => tag(n, t, !v.tags.includes(t))}>
                    {v.tags.includes(t) ? `untag ${t}` : `tag ${t}`}</button>
                ))}
              </div>
              {open[n] !== undefined && <div style={{ marginLeft: 24, marginTop: 6 }}><Diff text={open[n]} empty="No file change (an identity change caused by a dependency)." /></div>}
            </div>
          );
        })}
      </div>
      {compare && <Modal title={`v${compare.a} → ${compare.b === "current" ? "now" : `v${compare.b}`}`} onClose={() => setCompare(null)}>
        <Diff text={compare.text} /></Modal>}
      {viewing && <Modal title={`v${viewing.v} files`} onClose={() => setViewing(null)}>
        {Object.entries(viewing.parts).map(([k, t]) => <div key={k} className="stack"><h3>{k}</h3><pre className="panel">{t}</pre></div>)}
        {Object.keys(viewing.parts).length === 0 && <div className="muted">No files (archived state).</div>}
      </Modal>}
      {restoring !== null && (
        <Modal title={`Restore v${restoring} of ${id}`} onClose={() => setRestoring(null)}>
          <p className="muted small">The files of v{restoring} come back as a new version, checked like any change. Nothing in the history is lost.</p>
          <ChangeFlow saveLabel={`Restore v${restoring}`} send={async (o) => {
            const r = await call<ChangeResult>("POST", `/api/history/${kind}/${enc(id)}/restore`, { version: restoring, note: o.note },
              { dry_run: o.dryRun, confirm: o.confirm });
            return r.data;
          }} onDone={() => { setRestoring(null); h.reload(); onChanged(); }} />
        </Modal>
      )}
    </div>
  );
}

export function ResultsByVersion({ kind, id, rerun }: { kind: Kind; id: string; rerun?: string }) {
  const r = useLoad(() => get<{ grouped_by: string; versions: VersionStats[] }>(`/api/history/${kind}/${enc(id)}/results`), [kind, id]);
  if (r.error) return <ErrorNote error={r.error} />;
  if (!r.data) return <Loading what="results" />;
  const vs = r.data.versions;
  if (!vs.length) return (
    <div className="note">No scored runs of any version yet. {rerun && <Link to={rerun}>Run it in Build →</Link>}</div>
  );
  const best = Math.max(...vs.map((v) => v.mean_e0));
  return (
    <div className="stack">
      <table className="t">
        <thead><tr><th>Version</th><th>Runs</th><th>Mean E0</th><th></th><th>SUCCESS</th><th>Mean tokens</th><th>By {r.data.grouped_by}</th></tr></thead>
        <tbody>
          {vs.map((v) => (
            <tr key={String(v.version)}>
              <td><b>{typeof v.version === "number" ? `v${v.version}` : v.version}</b>{v.mean_e0 === best && vs.length > 1 && <> <span className="badge good">best</span></>}</td>
              <td>{v.n}</td><td>{v.mean_e0.toFixed(3)}</td>
              <td style={{ width: 140 }}><div className="bar"><i style={{ width: `${v.mean_e0 * 100}%` }} /></div></td>
              <td>{Math.round(v.success * 100)}%</td><td>{v.tokens.toLocaleString()}</td>
              <td className="small">{Object.entries(v.by).map(([k, x]) => <div key={k}>{k}: {x.mean_e0.toFixed(3)} (n={x.n})</div>)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="small muted">Mean E0 of clean, scored runs (before cost). Few runs per version are noisy: compare like with like (same tasks, seeds, model).
        {rerun && <> <Link to={rerun}>Re-run the affected experiments →</Link></>}</div>
    </div>
  );
}
