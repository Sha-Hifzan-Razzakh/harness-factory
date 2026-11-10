import { ReactNode, useCallback, useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ApiError, ChangeResult } from "../api";

export function useLoad<T>(fn: () => Promise<T>, deps: unknown[]): { data: T | null; error: string | null; loading: boolean; reload: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let live = true;
    setLoading(true);
    fn().then((d) => { if (live) { setData(d); setError(null); } })
      .catch((e) => { if (live) setError(e instanceof ApiError ? `API ${e.status}: ${e.message}` : String(e)); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, loading, reload };
}

export function Loading({ what }: { what?: string }) {
  return <div className="muted small">Loading{what ? ` ${what}` : ""}…</div>;
}

export function ErrorNote({ error }: { error: string | null }) {
  return error ? <div className="note bad">{error}</div> : null;
}

export function Diff({ text, empty = "No difference." }: { text: string; empty?: string }) {
  if (!text.trim()) return <div className="muted small">{empty}</div>;
  return (
    <div className="diff">
      {text.split("\n").map((line, i) => {
        if (line.startsWith("+++")) return <div key={i} className="file">{line.replace(/^\+\+\+ [^/]*\//, "")}</div>;
        if (line.startsWith("---")) return null;
        const cls = line.startsWith("@@") ? "hunk" : line.startsWith("+") ? "add" : line.startsWith("-") ? "del" : "";
        return <div key={i} className={cls}>{line || " "}</div>;
      })}
    </div>
  );
}

export function Markdown({ text }: { text: string }) {
  return <div className="md"><ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown></div>;
}

export function Modal({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) {
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="row"><h2 className="grow" style={{ margin: 0 }}>{title}</h2><button onClick={onClose}>Close</button></div>
        <div style={{ marginTop: 10 }}>{children}</div>
      </div>
    </div>
  );
}

export function when(at: string): string {
  const t = Date.parse(at);
  if (Number.isNaN(t)) return at;
  const s = (Date.now() - t) / 1000;
  const rel = s < 60 ? "just now" : s < 3600 ? `${Math.floor(s / 60)} min ago` : s < 86400 ? `${Math.floor(s / 3600)} h ago`
    : `${Math.floor(s / 86400)} d ago`;
  return rel;
}

export function stamp(at: string): string {
  return at.replace("T", " ").replace("Z", " UTC");
}

const ACTION_BADGE: Record<string, string> = {
  create: "good", update: "accent", restore: "good", archive: "bad", external: "warn", baseline: "", dependency: "",
  purge: "bad", tag: "accent",
};

export function ActionBadge({ action }: { action: string }) {
  return <span className={`badge ${ACTION_BADGE[action] ?? ""}`}>{action}</span>;
}

/** What a change did or would do: status, issues, impact, notes, the diff. */
export function ChangeView({ r, showDiff = true }: { r: ChangeResult; showDiff?: boolean }) {
  const tone = { applied: "good", dry_run: "", needs_confirm: "warn", rejected: "bad" }[r.status];
  const imp = r.impact;
  const env = Object.entries(imp.env_changed ?? {}).filter(([, rs]) => rs.length);
  return (
    <div className="stack">
      <div className={`note ${tone}`}>
        <b>{{ applied: "Saved", dry_run: "Checks pass", needs_confirm: "Needs your confirmation", rejected: "Rejected: nothing was written" }[r.status]}</b>
        {r.version ? <> · recorded as <b>v{r.version}</b></> : null}
        {r.status === "needs_confirm" ? " · this changes the identity of existing experiments" : null}
      </div>
      {r.issues.map((i, k) => <div key={k} className="note bad"><b>{i.where}</b>: {i.detail}</div>)}
      {(imp.recipes_changed.length > 0 || env.length > 0 || imp.models_changed.length > 0 || imp.recipes_added.length > 0) && (
        <div className="note warn small">
          {imp.recipes_added.length > 0 && <div>New recipes: {imp.recipes_added.join(", ")}</div>}
          {imp.recipes_changed.length > 0 && <div>Recipe hash changes (new cells from now on): <b>{imp.recipes_changed.join(", ")}</b></div>}
          {env.map(([s, rs]) => <div key={s}>Tool environment of <b>{s}</b> changes for: {rs.join(", ")}</div>)}
          {imp.models_changed.length > 0 && <div>Model identity changes: {imp.models_changed.join(", ")}</div>}
          {imp.stale_results ? <div>{imp.stale_results} stored run(s) stay on the old identity (kept as history).</div> : null}
        </div>
      )}
      {r.notes.map((n, k) => <div key={k} className="note small">{n}</div>)}
      {showDiff && r.diff ? <Diff text={r.diff} /> : null}
    </div>
  );
}
