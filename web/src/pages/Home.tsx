import { Link } from "react-router-dom";
import { Entry, get, kindLabel } from "../api";
import { ActionBadge, useLoad, when } from "../components/common";

const LOOP = [
  ["Change", "/workspace", "Edit a harness, skill, prompt, tool, fault profile or model. Say why. Every save is a version; Check shows the diff and which recipes it re-hashes before you save."],
  ["Spec", "/build", "Pick tasks × harnesses × models. Each cell is a spec card: the exact tools and prompts the model will see, budgets, cost estimate, READY or BLOCKED."],
  ["Run", "/build", "Plan shows the projection per quota bucket; Start runs it in fresh sandboxes, paced to the provider's limits."],
  ["Measure", "/results", "Cells with E and its 95% interval; the final spec cards land in the grid and under Cards."],
  ["Compare versions", "/workspace", "Each item's Results tab groups runs by the version they ran with: did your change help? Restore or tag the best version."],
];

const PLAYBOOKS: [string, string[]][] = [
  ["Improve a skill (or a prompt)", [
    "Workspace → Skills → pick it → Edit. Change the text, write why, Check: see the diff and which recipes get a new hash.",
    "Save. The recipes that use it get a dependency version automatically.",
    "Results by version → 'Re-run the affected experiments' opens Build with those recipes; run the same tasks and seeds as before.",
    "Compare v(n) with v(n-1) in Results by version. Keep it, tag it 'best', or Restore the old version (nothing is lost)."]],
  ["Try a one-change variant of a harness", [
    "Workspace → Harnesses → New: base + the one field you change (Reference → Recipe fields lists them all with defaults).",
    "Build: both the base and the variant on the same tasks, 3+ seeds.",
    "Results: are the 95% intervals apart? The campaign report pairs runs by task and seed."]],
  ["Add or re-price a model", [
    "Workspace → Models → New (or Edit). Prices change no identity; a new provider model id does (it asks to confirm).",
    "Build with scripted/replay first (free), then the model on a few tasks; read the cost estimate on the spec cards."]],
  ["Test robustness", [
    "Build: pick a fault profile (first-call-fails is the clearest). Clean runs are added next to it automatically.",
    "Results: the R column = faulted ÷ clean outcome."]],
  ["Someone edited files outside the workspace", [
    "Activity → Sync outside changes: each changed item gets an 'external' version with its diff.",
    "From then on its history and results by version include that change."]],
];

const GLOSSARY: [string, string][] = [
  ["E0", "The run's stored score: weighted mean of outcome O and trajectory Q (Q counted as Q×O); 0 on any failed guard."],
  ["E", "max(0, E0 − w·C): cost-adjusted, per cell, at aggregation. What harnesses are ranked by."],
  ["C", "Cost against the task's median: 0 free, 0.5 the usual cost, 1 twice or more."],
  ["R", "Robustness: mean outcome of faulted ÷ clean runs, capped at 1."],
  ["Version", "Every saved change to an item: who, when, why, its files before and after, and what it re-hashed."],
  ["Dependency version", "A recipe whose hash changed because something it uses (a skill, a prompt, its base) changed."],
  ["RunKey / hash", "Task, recipe, model, provider model id, tool specs, seed and fault hashed together; equal key = same experiment."],
  ["Cell", "Task class × harness × model × provider × environment: what results average over."],
  ["Archive", "Delete keeps history: the item leaves the catalog and can be restored. Purge only items no run ever used."],
];

export default function Home() {
  const recent = useLoad(() => get<Entry[]>("/api/activity", { limit: 8 }), []);
  const issues = useLoad(() => get<{ where: string; detail: string }[]>("/api/check"), []);
  return (
    <div className="stack">
      <div><h1>Harness Factory</h1>
        <div className="sub">Which agent harness works best for which kind of task on which model, measured on the environment, never on the model's claims.</div></div>
      {(issues.data ?? []).length > 0 && <div className="note bad">{issues.data!.length} catalog issue(s): {issues.data!.slice(0, 3).map((i) => `${i.where}: ${i.detail}`).join(" · ")}</div>}
      <div className="panel">
        <h2 style={{ marginTop: 0 }}>The experiment loop</h2>
        <div className="row" style={{ alignItems: "stretch" }}>
          {LOOP.map(([t, to, d], i) => (
            <Link key={t} to={to} className="panel grow" style={{ minWidth: 170, flexBasis: 0, color: "var(--ink)" }}>
              <div className="muted small">{i + 1}</div><b>{t}</b><div className="small muted" style={{ marginTop: 4 }}>{d}</div></Link>
          ))}
        </div>
      </div>
      <div className="split" style={{ gridTemplateColumns: "1fr 380px" }}>
        <div className="panel">
          <h2 style={{ marginTop: 0 }}>Playbooks</h2>
          {PLAYBOOKS.map(([t, steps]) => (
            <details key={t} style={{ marginBottom: 8 }}><summary><b>{t}</b></summary>
              <ol>{steps.map((s) => <li key={s}>{s}</li>)}</ol></details>
          ))}
          <h2>Rules that keep the numbers honest</h2>
          <ul className="small">
            <li>A run that does nothing scores 0; "don't touch anything else" checks are guards.</li>
            <li>Only clean, scored runs are averaged; INCOMPLETE (provider) and ERROR (setup) runs are excluded.</li>
            <li>Changing an item changes the hash of what uses it: old results stay as history, new runs start new cells.</li>
            <li>Compare versions on the same tasks, seeds and model. Start offline with scripted models; use Docker sandboxes for real ones.</li>
          </ul>
          <h2>Glossary</h2>
          <table className="t"><tbody>{GLOSSARY.map(([t, d]) => <tr key={t}><td><b>{t}</b></td><td className="small">{d}</td></tr>)}</tbody></table>
        </div>
        <div className="panel">
          <div className="row"><h2 className="grow" style={{ margin: 0 }}>Recent changes</h2><Link to="/activity">all →</Link></div>
          {(recent.data ?? []).filter((e) => e.action !== "baseline").slice(0, 8).map((e) => (
            <div key={e.seq} style={{ padding: "6px 0", borderBottom: "1px solid var(--line)" }}>
              <div className="row small"><ActionBadge action={e.action} /><Link to={`/workspace/${e.kind}/${e.id}?tab=history`}>{kindLabel(e.kind)} {e.id}</Link>
                {e.version !== null && <span className="badge accent">v{e.version}</span>}</div>
              <div className="small muted">{e.note || "no reason given"} · {e.author} · {when(e.at)}</div>
            </div>
          ))}
          {recent.data && !recent.data.filter((e) => e.action !== "baseline").length && <div className="muted small">No changes recorded yet.</div>}
        </div>
      </div>
    </div>
  );
}
