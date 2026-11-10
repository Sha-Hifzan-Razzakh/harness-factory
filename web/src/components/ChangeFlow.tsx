import { useState } from "react";
import { ChangeResult } from "../api";
import { ChangeView, Modal } from "./common";

type Send = (opts: { dryRun: boolean; confirm: boolean; note: string }) => Promise<ChangeResult>;

/**
 * The one way every change is saved: write why, Check (a dry run: diff, impact, issues), Save. When the change alters
 * the identity of existing experiments the server answers needs_confirm, and a dialog shows exactly what changes.
 */
export function ChangeFlow({ send, saveLabel = "Save new version", onDone, requireNote = true, danger = false }: {
  send: Send; saveLabel?: string; onDone?: (r: ChangeResult) => void; requireNote?: boolean; danger?: boolean;
}) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [result, setResult] = useState<ChangeResult | null>(null);
  const [confirming, setConfirming] = useState<ChangeResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run(dryRun: boolean, confirm: boolean) {
    setBusy(dryRun ? "Checking with the factory's validators…" : "Saving…");
    setError(null);
    try {
      const r = await send({ dryRun, confirm, note });
      if (r.status === "needs_confirm" && !dryRun) setConfirming(r);
      else setResult(r);
      if (r.status === "applied") onDone?.(r);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }

  const noteMissing = requireNote && !note.trim();
  return (
    <div className="stack">
      <label className="field">
        <span>Why (recorded with the version; required)</span>
        <input style={{ width: "100%" }} value={note} onChange={(e) => setNote(e.target.value)}
               placeholder="e.g. tell the model to re-read files after writing" />
      </label>
      <div className="row">
        <button onClick={() => run(true, false)} disabled={!!busy}>Check changes</button>
        <button className={danger ? "danger" : "primary"} onClick={() => run(false, false)} disabled={!!busy || noteMissing}
                title={noteMissing ? "Say why first" : ""}>{saveLabel}</button>
        {busy && <span className="muted small">{busy}</span>}
      </div>
      {error && <div className="note bad">{error}</div>}
      {result && <ChangeView r={result} />}
      {confirming && (
        <Modal title="This changes existing experiments" onClose={() => setConfirming(null)}>
          <ChangeView r={confirming} />
          <div className="row" style={{ marginTop: 12 }}>
            <span className="grow muted small">Old results stay as history under the old identity; new runs start new cells.</span>
            <button onClick={() => setConfirming(null)}>Cancel</button>
            <button className="primary" onClick={() => { setConfirming(null); run(false, true); }}>Confirm and save</button>
          </div>
        </Modal>
      )}
    </div>
  );
}
