import yaml from "js-yaml";
import { useEffect, useState } from "react";
import { call, ChangeResult, enc, get, Item, Kind } from "../api";
import { ChangeFlow } from "./ChangeFlow";
import { ErrorNote, Loading } from "./common";

async function change(method: string, path: string, body: unknown, o: { dryRun: boolean; confirm: boolean; note: string }) {
  const r = await call<ChangeResult | { detail: unknown }>(method, path, body, { dry_run: o.dryRun, confirm: o.confirm, note: o.note });
  if (r.status >= 400 && !(r.data && typeof r.data === "object" && "status" in r.data)) {
    throw new Error(`API ${r.status}: ${JSON.stringify((r.data as { detail?: unknown })?.detail ?? r.data)}`);
  }
  return r.data as ChangeResult;
}

/** Edit an item's own files as text: exactly what is stored, so the diff shows only what you changed. */
export function FilesEditor({ kind, id, onSaved }: { kind: Kind; id: string; onSaved: () => void }) {
  const [parts, setParts] = useState<Record<string, string> | null>(null);
  const [orig, setOrig] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    get<{ parts: Record<string, string> }>(`/api/history/${kind}/${enc(id)}/version/current`)
      .then((d) => { setParts(d.parts); setOrig(d.parts); }).catch((e) => setError(String(e)));
  }, [kind, id]);
  if (error) return <ErrorNote error={error} />;
  if (!parts) return <Loading what="files" />;
  const dirty = Object.keys(parts).some((k) => parts[k] !== orig[k]);
  return (
    <div className="stack">
      {Object.entries(parts).map(([k, v]) => (
        <label key={k} className="field">
          <span>{k.replace("#", "  ›  ")}</span>
          <textarea className="mono" spellCheck={false} value={v} rows={Math.min(30, Math.max(8, v.split("\n").length + 1))}
                    onChange={(e) => setParts({ ...parts, [k]: e.target.value })} />
        </label>
      ))}
      <div className="row small muted">{dirty ? "Unsaved changes" : "No changes yet"}
        {dirty && <button className="link" onClick={() => setParts(orig)}>Discard</button>}</div>
      <ChangeFlow send={(o) => change("PUT", `/api/parts/${kind}/${enc(id)}`, { parts, note: o.note }, o)}
                  onDone={() => { setOrig(parts); onSaved(); }} />
    </div>
  );
}

/** Tool servers and new items: the create/update payload as YAML (the server checks code and discovers tools). */
export function PayloadEditor({ kind, id, initial, method, onSaved, saveLabel }: {
  kind: Kind; id?: string; initial: unknown; method: "POST" | "PUT"; onSaved: (r: ChangeResult) => void; saveLabel?: string;
}) {
  const [text, setText] = useState(() => yaml.dump(initial, { lineWidth: 110, noRefs: true }));
  const [parseError, setParseError] = useState<string | null>(null);
  const body = () => {
    try {
      const v = yaml.load(text);
      setParseError(null);
      return v;
    } catch (e) {
      setParseError(String(e));
      throw e;
    }
  };
  const path = method === "POST" ? `/api/catalog/${kind}` : `/api/catalog/${kind}/${enc(id ?? "")}`;
  return (
    <div className="stack">
      <textarea className="mono" spellCheck={false} value={text} rows={Math.min(34, Math.max(10, text.split("\n").length + 1))}
                onChange={(e) => setText(e.target.value)} />
      {parseError && <div className="note bad">Not valid YAML: {parseError}</div>}
      <ChangeFlow saveLabel={saveLabel} send={(o) => change(method, path, body(), o)} onDone={onSaved} />
    </div>
  );
}

export interface ModelForm {
  provider: string; canonical: string; provider_model_id: string; price_in: number; price_out: number; free: boolean;
  caps: { tool_calling: string; structured_output: boolean; context_len: number }; limits?: Record<string, unknown>;
}

/** Models: a form (providers.yaml keeps its comments; only this model's line changes). */
export function ModelEditor({ initial, providers, isNew, onSaved }: {
  initial: ModelForm; providers: string[]; isNew: boolean; onSaved: (r: ChangeResult) => void;
}) {
  const [m, setM] = useState<ModelForm>(initial);
  const set = (patch: Partial<ModelForm>) => setM({ ...m, ...patch });
  const caps = (patch: Partial<ModelForm["caps"]>) => setM({ ...m, caps: { ...m.caps, ...patch } });
  const id = `${m.provider}/${m.canonical}`;
  return (
    <div className="stack">
      <div className="row">
        <label className="field"><span>Provider</span>
          <select value={m.provider} disabled={!isNew} onChange={(e) => set({ provider: e.target.value })}>
            {providers.map((p) => <option key={p}>{p}</option>)}</select></label>
        <label className="field grow"><span>Canonical model (the experiment variable)</span>
          <input style={{ width: "100%" }} value={m.canonical} disabled={!isNew} onChange={(e) => set({ canonical: e.target.value })} /></label>
        <label className="field grow"><span>Provider model id (in every run's environment hash)</span>
          <input style={{ width: "100%" }} value={m.provider_model_id} onChange={(e) => set({ provider_model_id: e.target.value })} /></label>
      </div>
      <div className="row">
        <label className="field"><span>Price in ($/M tokens)</span><input type="number" step="0.01" value={m.price_in} onChange={(e) => set({ price_in: Number(e.target.value) })} /></label>
        <label className="field"><span>Price out ($/M tokens)</span><input type="number" step="0.01" value={m.price_out} onChange={(e) => set({ price_out: Number(e.target.value) })} /></label>
        <label className="field"><span>Free tier</span><input type="checkbox" checked={m.free} onChange={(e) => set({ free: e.target.checked })} /></label>
        <label className="field"><span>Context window (tokens)</span><input type="number" value={m.caps.context_len} onChange={(e) => caps({ context_len: Number(e.target.value) })} /></label>
        <label className="field"><span>Tool calling</span>
          <select value={m.caps.tool_calling} onChange={(e) => caps({ tool_calling: e.target.value })}>
            <option>native</option><option>emulated</option><option>none</option></select></label>
        <label className="field"><span>Structured output</span><input type="checkbox" checked={m.caps.structured_output} onChange={(e) => caps({ structured_output: e.target.checked })} /></label>
      </div>
      <div className="small muted">Prices are not part of any hash: changing them changes no identity. Verify ids and limits against the provider's live docs.</div>
      <ChangeFlow saveLabel={isNew ? "Add model" : "Save new version"}
                  send={(o) => change(isNew ? "POST" : "PUT", isNew ? "/api/catalog/model" : `/api/catalog/model/${enc(id)}`, m, o)}
                  onDone={onSaved} />
    </div>
  );
}

/** Archive (delete): the item leaves the catalog; its history stays and Restore brings it back. */
export function ArchivePanel({ item, onDone }: { item: Item; onDone: () => void }) {
  if (item.builtin) return <div className="note small">Built-in: archived only by editing the repository.</div>;
  return (
    <div className="stack">
      {item.used_by.length > 0 && (
        <div className="note warn small">Still used by {item.used_by.join(", ")}: archiving will be refused until nothing uses it.</div>
      )}
      <ChangeFlow danger saveLabel="Archive" send={(o) => change("DELETE", `/api/catalog/${item.kind}/${enc(item.id)}`, undefined, o)}
                  onDone={onDone} />
    </div>
  );
}
