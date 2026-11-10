import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Card, ChangeResult, enc, Entry, get, Item, Kind, KINDS, kindLabel } from "../api";
import { ArchivePanel, FilesEditor, ModelEditor, ModelForm, PayloadEditor } from "../components/Editors";
import { HistoryView, ResultsByVersion } from "../components/HistoryView";
import { ErrorNote, Loading, Markdown, stamp, useLoad, when } from "../components/common";

type Latest = Record<string, Entry>;

const NEW_TEMPLATES: Record<string, unknown> = {
  skill: { id: "my-skill", name: "A title for the how-to", description: "One line: when it applies.", body: "1. ...\n2. ...\n" },
  prompt: { ref: "system/my-prompt", text: "You are an automation agent...\n{% if tools %}Available tools: {{ tools | join(', ') }}.{% endif %}\n" },
  recipe: { id: "v2-my-change", base: "v2-base", note: "Matrix (config): v2-base with one change and why.", fields: { config: { max_steps: 12 } } },
  fault: { id: "my-fault", description: "What fails and how often.", rules: [{ tools: ["*"], kind: "error", rate: 0.1 }] },
  server: { id: "calc", transport: "stdio", instructions: "", tools: [{ name: "add", description: "Add two numbers.", side_effect: "READ", risk: "R0",
    timeout_s: 10, params: [{ name: "a", type: "float" }, { name: "b", type: "float" }] }] },
};

const CARD_KIND: Partial<Record<Kind, string>> = { recipe: "harness", task: "task", server: "tool", fault: "fault", model: "model" };

function useLatest() {
  return useLoad(async () => {
    const entries = await get<Entry[]>("/api/activity", { limit: 100000 });
    const latest: Latest = {};
    for (const e of [...entries].reverse()) if (e.version !== null && e.action !== "tag") latest[`${e.kind}:${e.id}`] = e;
    return { latest };
  }, []);
}

export default function Workspace() {
  const params = useParams();
  const kind = (params.kind ?? "recipe") as Kind | "archived";
  const id = params["*"] || "";
  const nav = useNavigate();
  const [q, setQ] = useState("");
  const items = useLoad(() => (kind === "archived" ? Promise.resolve([] as Item[]) : get<Item[]>(`/api/catalog/${kind}`)), [kind]);
  const archived = useLoad(() => get<Entry[]>("/api/archived"), [kind]);
  const latest = useLatest();
  const reloadAll = () => { items.reload(); latest.reload(); archived.reload(); };

  const shown = useMemo(() => (items.data ?? []).filter((it) =>
    !q || `${it.id} ${it.summary}`.toLowerCase().includes(q.toLowerCase())), [items.data, q]);

  return (
    <div>
      <div className="topbar">
        <div><h1>Workspace</h1><div className="sub">Everything experiments are made of. Every save is a version: who, when, why, and what it changed.</div></div>
      </div>
      <div className="row" style={{ marginBottom: 10 }}>
        {KINDS.map((k) => (
          <Link key={k.kind} to={`/workspace/${k.kind}`} className={`chip ${kind === k.kind ? "on" : ""}`}>{k.label}</Link>
        ))}
        <Link to="/workspace/archived" className={`chip ${kind === "archived" ? "on" : ""}`}>Archived ({archived.data?.length ?? 0})</Link>
      </div>
      {kind === "archived" ? <ArchivedList entries={archived.data ?? []} /> : (
        <div className="split">
          <div className="panel">
            <div className="row">
              <input className="grow" placeholder={`Filter ${kindLabel(kind).toLowerCase()}`} value={q} onChange={(e) => setQ(e.target.value)} />
              {KINDS.find((k) => k.kind === kind)?.editable && <button className="primary" onClick={() => nav(`/workspace/${kind}/~new`)}>New</button>}
            </div>
            <div className="small muted" style={{ margin: "6px 2px" }}>{KINDS.find((k) => k.kind === kind)?.help}</div>
            {items.loading && <Loading />}
            <ErrorNote error={items.error} />
            <div className="list">
              {shown.map((it) => {
                const e = latest.data?.latest[`${it.kind}:${it.id}`];
                return (
                  <div key={it.id} className={`it ${it.id === id ? "on" : ""}`} onClick={() => nav(`/workspace/${kind}/${it.id}`)}>
                    <div className="row"><span className="id grow">{it.id}</span>
                      {e ? <span className="badge accent">v{e.version}</span> : <span className="badge">no history</span>}
                      {it.issues.length > 0 && <span className="badge bad">{it.issues.length} issue</span>}
                      {it.builtin && <span className="badge">built-in</span>}</div>
                    <div className="meta">{e ? <>{when(e.at)} · {e.note || e.action}</> : it.summary}</div>
                    {it.used_by.length > 0 && <div className="meta">used by {it.used_by.length}</div>}
                  </div>
                );
              })}
            </div>
          </div>
          <div>
            {id === "~new" ? <NewItem kind={kind} onCreated={(newId) => { reloadAll(); nav(`/workspace/${kind}/${newId}`); }} />
              : id ? <ItemView kind={kind} id={id} onChanged={reloadAll} onGone={() => { reloadAll(); nav(`/workspace/${kind}`); }} />
              : <div className="panel muted">Pick an item on the left, or create one with New.</div>}
          </div>
        </div>
      )}
    </div>
  );
}

function ArchivedList({ entries }: { entries: Entry[] }) {
  if (!entries.length) return <div className="panel muted">Nothing is archived.</div>;
  return (
    <div className="panel">
      <table className="t"><thead><tr><th>Item</th><th>Archived</th><th>By</th><th>Why</th><th></th></tr></thead>
        <tbody>{entries.map((e) => (
          <tr key={e.seq}><td><b>{kindLabel(e.kind)}</b> {e.id}</td><td title={stamp(e.at)}>{when(e.at)}</td><td>{e.author}</td><td>{e.note}</td>
            <td><Link to={`/workspace/${e.kind}/${e.id}?tab=history`}>History and restore →</Link></td></tr>
        ))}</tbody></table>
    </div>
  );
}

function rerunLink(kind: Kind, item: Item | null): string | undefined {
  if (!item) return undefined;
  if (kind === "recipe") return `/build?recipes=${item.id}`;
  const recipes = item.used_by.filter((u) => u.startsWith("recipe ")).map((u) => u.split(" ")[1]);
  if (kind === "model") return `/build?model=${item.id}`;
  return recipes.length ? `/build?recipes=${recipes.join(",")}` : undefined;
}

function ItemView({ kind, id, onChanged, onGone }: { kind: Kind; id: string; onChanged: () => void; onGone: () => void }) {
  const tabFromUrl = new URLSearchParams(window.location.hash.split("?")[1] ?? "").get("tab");
  const [tab, setTab] = useState(tabFromUrl ?? "overview");
  const [tick, setTick] = useState(0);
  const item = useLoad(() => get<Item>(`/api/catalog/${kind}/${enc(id)}`), [kind, id, tick]);
  const editable = KINDS.find((k) => k.kind === kind)?.editable && !item.data?.builtin;
  const changed = () => { setTick((t) => t + 1); onChanged(); };
  const gone = !!item.error && item.error.startsWith("API 404");
  return (
    <div className="panel">
      <div className="row"><h2 className="grow" style={{ margin: 0 }}>{kindLabel(kind)} · {id}</h2>
        {item.data?.hash && <code className="small muted">{item.data.hash}</code>}</div>
      {item.data && <div className="small muted">{item.data.summary}</div>}
      <div className="tabs">
        {["overview", "edit", "history", "results"].map((t) => (
          <button key={t} className={tab === t ? "on" : ""} onClick={() => setTab(t)}>
            {{ overview: "Overview", edit: editable ? "Edit / archive" : "Files", history: "History", results: "Results by version" }[t]}
          </button>
        ))}
      </div>
      {gone && tab !== "history" ? <div className="note warn">This item is not in the catalog (archived or removed). Its History tab can restore it.</div>
        : tab === "overview" ? <Overview kind={kind} item={item.data} error={item.error} />
        : tab === "edit" ? (item.data ? <EditTab kind={kind} item={item.data} editable={!!editable} onSaved={changed} onArchived={onGone} /> : <Loading />)
        : tab === "history" ? <HistoryView kind={kind} id={id} onChanged={changed} />
        : <ResultsByVersion kind={kind} id={id} rerun={rerunLink(kind, item.data)} />}
    </div>
  );
}

function Overview({ kind, item, error }: { kind: Kind; item: Item | null; error: string | null }) {
  const cardKind = CARD_KIND[kind];
  const cardId = kind === "model" ? item?.id.split("/").slice(1).join("/") : item?.id;
  const card = useLoad(() => (cardKind && cardId ? get<Card>(`/api/cards/${cardKind}/${enc(cardId)}`) : Promise.resolve(null)), [cardKind, cardId]);
  if (error) return <ErrorNote error={error} />;
  if (!item) return <Loading />;
  return (
    <div className="stack">
      {item.issues.map((i, k) => <div key={k} className="note bad">{i}</div>)}
      <div className="row small">
        <span><b>Used by:</b> {item.used_by.length ? item.used_by.join(", ") : "nothing"}</span>
      </div>
      {item.path && <div className="small muted">File: {item.path}</div>}
      {card.data ? <Markdown text={card.data.markdown} /> : card.loading && cardKind ? <Loading what="card" />
        : item.text ? <pre className="panel">{item.text}</pre> : null}
    </div>
  );
}

function EditTab({ kind, item, editable, onSaved, onArchived }: { kind: Kind; item: Item; editable: boolean; onSaved: () => void; onArchived: () => void }) {
  const payload = useLoad(() => (kind === "server" || kind === "model") && editable
    ? get<Record<string, unknown>>(`/api/payload/${kind}/${enc(item.id)}`) : Promise.resolve(null), [kind, item.id]);
  const providers = useLoad(() => get<{ provider: string }[]>("/api/models"), []);
  if (!editable) return (
    <div className="stack">
      <div className="note small">{kind === "task" ? "Tasks are written by hand (reference solution, trap tests, do-nothing check). Edit the files in the repo; Sync records the change as a version."
        : "Built-in: edit it in the repository; Sync records the change as a version."}</div>
      <pre className="panel">{item.text}</pre>
    </div>
  );
  return (
    <div className="stack">
      {kind === "server" ? (payload.data ? <>
          <div className="small muted">The server's declaration (and code, for a local server). Saving checks the code, starts the server and discovers its tools.</div>
          <PayloadEditor kind={kind} id={item.id} initial={payload.data} method="PUT" onSaved={onSaved} />
        </> : <Loading />)
        : kind === "model" ? (payload.data ? <ModelEditor initial={payload.data as unknown as ModelForm} isNew={false}
            providers={(providers.data ?? []).map((p) => p.provider)} onSaved={onSaved} /> : <Loading />)
        : <FilesEditor kind={kind} id={item.id} onSaved={onSaved} />}
      <h3>Archive</h3>
      <ArchivePanel item={item} onDone={onArchived} />
    </div>
  );
}

function NewItem({ kind, onCreated }: { kind: Kind; onCreated: (id: string) => void }) {
  const providers = useLoad(() => get<{ provider: string }[]>("/api/models"), []);
  const done = (r: ChangeResult) => onCreated(r.id);
  return (
    <div className="panel stack">
      <h2 style={{ margin: 0 }}>New {kindLabel(kind).replace(/s$/, "").toLowerCase()}</h2>
      {kind === "model" ? (
        <ModelEditor isNew providers={(providers.data ?? []).map((p) => p.provider).filter((p) => p !== "scripted-judge")}
                     initial={{ provider: "openrouter", canonical: "", provider_model_id: "", price_in: 0, price_out: 0, free: false,
                       caps: { tool_calling: "native", structured_output: true, context_len: 32768 } }} onSaved={done} />
      ) : (
        <>
          <div className="small muted">{kind === "recipe" ? "A matrix recipe: a base plus the one dimension you change (fields). See Reference for every field and its default."
            : kind === "server" ? "Without code a stub is generated; fill it in and save again. A new capability goes on a new server."
            : "Edit the YAML below. Check shows exactly what would be written."}</div>
          <PayloadEditor kind={kind} initial={NEW_TEMPLATES[kind]} method="POST" saveLabel="Create" onSaved={done} />
        </>
      )}
    </div>
  );
}

