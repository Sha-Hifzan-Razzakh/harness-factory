import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { call, enc, Entry, get, KINDS, kindLabel } from "../api";
import { ActionBadge, Diff, ErrorNote, Loading, stamp, useLoad, when } from "../components/common";

export default function Activity() {
  const feed = useLoad(() => get<Entry[]>("/api/activity", { limit: 2000 }), []);
  const [kind, setKind] = useState("");
  const [action, setAction] = useState("");
  const [author, setAuthor] = useState("");
  const [hideBaselines, setHideBaselines] = useState(true);
  const [open, setOpen] = useState<Record<number, string>>({});
  const [syncMsg, setSyncMsg] = useState<string | null>(null);

  const rows = useMemo(() => (feed.data ?? []).filter((e) =>
    (!kind || e.kind === kind) && (!action || e.action === action) && (!author || e.author === author)
    && !(hideBaselines && e.action === "baseline")), [feed.data, kind, action, author, hideBaselines]);
  const authors = [...new Set((feed.data ?? []).map((e) => e.author))];
  const actions = [...new Set((feed.data ?? []).map((e) => e.action))];
  const byDay = useMemo(() => {
    const out: [string, Entry[]][] = [];
    for (const e of rows) {
      const day = e.at.slice(0, 10);
      if (!out.length || out[out.length - 1][0] !== day) out.push([day, []]);
      out[out.length - 1][1].push(e);
    }
    return out;
  }, [rows]);

  async function toggle(e: Entry) {
    if (open[e.seq] !== undefined) { const o = { ...open }; delete o[e.seq]; setOpen(o); return; }
    const v = e.version ?? 0;
    const text = v > 1 ? (await get<{ diff: string }>(`/api/history/${e.kind}/${enc(e.id)}/diff`, { a: String(v - 1), b: String(v) })).diff
      : "(first version)";
    setOpen({ ...open, [e.seq]: text });
  }

  async function sync() {
    setSyncMsg("Looking for changes made outside the workspace…");
    const r = await call<Entry[]>("POST", "/api/sync");
    const made = (r.data ?? []) as Entry[];
    const ext = made.filter((m) => m.action !== "baseline").length;
    const base = made.length - ext;
    setSyncMsg(made.length ? `${ext} outside change(s) recorded${base ? `; history started for ${base} item(s)` : ""}.` : "Nothing changed outside the workspace.");
    feed.reload();
  }

  return (
    <div>
      <div className="topbar">
        <div><h1>Activity</h1><div className="sub">Every change to the catalog, newest first: who, when, why, and what it re-hashed.</div></div>
        <button onClick={sync}>Sync outside changes</button>
      </div>
      {syncMsg && <div className="note" style={{ marginBottom: 10 }}>{syncMsg}</div>}
      <div className="panel row" style={{ marginBottom: 12 }}>
        <select value={kind} onChange={(e) => setKind(e.target.value)}><option value="">all kinds</option>
          {KINDS.map((k) => <option key={k.kind} value={k.kind}>{k.label}</option>)}</select>
        <select value={action} onChange={(e) => setAction(e.target.value)}><option value="">all actions</option>
          {actions.map((a) => <option key={a}>{a}</option>)}</select>
        <select value={author} onChange={(e) => setAuthor(e.target.value)}><option value="">everyone</option>
          {authors.map((a) => <option key={a}>{a}</option>)}</select>
        <label className="row small"><input type="checkbox" checked={hideBaselines} onChange={(e) => setHideBaselines(e.target.checked)} />hide history starts</label>
        <span className="grow" /><span className="muted small">{rows.length} change(s)</span>
      </div>
      <ErrorNote error={feed.error} />
      {feed.loading && <Loading />}
      {!feed.loading && !rows.length && <div className="panel muted">No changes yet. Edit something in the Workspace, or Sync to start the history.</div>}
      {byDay.map(([day, es]) => (
        <div key={day}>
          <h3 className="muted">{day}</h3>
          <div className="panel">
            {es.map((e) => (
              <div key={e.seq} style={{ padding: "6px 0", borderBottom: "1px solid var(--line)" }}>
                <div className="row">
                  <span className="muted small" title={stamp(e.at)} style={{ width: 80 }}>{e.at.slice(11, 16)}</span>
                  <ActionBadge action={e.action} />
                  <Link to={`/workspace/${e.kind}/${e.id}?tab=history`}><b>{kindLabel(e.kind)}</b> {e.id}</Link>
                  {e.version !== null && <span className="badge accent">v{e.version}</span>}
                  {e.tags.map((t) => <span key={t} className="badge good">{t}</span>)}
                  <span className="grow">{e.note || <span className="muted">no reason given</span>}{e.cause && <span className="muted"> · because of {e.cause}</span>}</span>
                  <span className="muted small">{e.author} · {when(e.at)}</span>
                  {e.action !== "tag" && e.action !== "purge" && <button className="link small" onClick={() => toggle(e)}>{open[e.seq] !== undefined ? "hide" : "changes"}</button>}
                </div>
                {(e.impact?.recipes_changed?.length ?? 0) > 0 && <div className="small muted" style={{ marginLeft: 88 }}>re-hashed: {e.impact.recipes_changed!.join(", ")}</div>}
                {open[e.seq] !== undefined && <div style={{ margin: "6px 0 4px 88px" }}><Diff text={open[e.seq]} empty="No file change (identity changed through a dependency)." /></div>}
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
