// Every call goes to the Harness Factory API (same origin when served by `hfx web`; proxied in `npm run dev`).
// The bearer token (HF_API_TOKEN) is asked for once and kept in this browser's localStorage.

const TOKEN_KEY = "hf-token";

export class ApiError extends Error {
  constructor(public status: number, public body: unknown) {
    super(typeof body === "object" && body && "detail" in body ? JSON.stringify((body as { detail: unknown }).detail) : String(body));
  }
}

export function getToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) ?? "";
  } catch {
    return "";
  }
}

export function setToken(t: string) {
  try {
    localStorage.setItem(TOKEN_KEY, t);
  } catch {
    /* private window: the token lives for this page only */
  }
}

export async function call<T = unknown>(method: string, path: string, body?: unknown,
                                        params?: Record<string, string | number | boolean | undefined | null>): Promise<{ status: number; data: T }> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params ?? {})) if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(`${path}${qs.toString() ? `?${qs}` : ""}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  const text = await res.text();
  let data: unknown = text;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    /* plain text */
  }
  return { status: res.status, data: data as T };
}

export async function get<T = unknown>(path: string, params?: Record<string, string | number | boolean | undefined>): Promise<T> {
  const r = await call<T>("GET", path, undefined, params);
  if (r.status >= 400) throw new ApiError(r.status, r.data);
  return r.data;
}

export const enc = (id: string) => id.split("/").map(encodeURIComponent).join("/");

// ---- shapes of what the API returns ------------------------------------------------------------------------------

export type Kind = "recipe" | "skill" | "prompt" | "server" | "fault" | "model" | "task";

export interface Item {
  kind: Kind; id: string; path?: string | null; summary: string; hash?: string | null; used_by: string[];
  builtin: boolean; issues: string[]; detail: Record<string, unknown>; text?: string | null;
}

export interface Impact {
  recipes_changed: string[]; recipes_added: string[]; recipes_removed: string[];
  env_changed: Record<string, string[]>; stale_results: number | null; models_changed: string[];
}

export interface ChangeResult {
  kind: string; id: string; action: string; status: "applied" | "dry_run" | "needs_confirm" | "rejected";
  version: number | null; diff: string; files: { path: string; action: string }[];
  issues: { where: string; detail: string }[]; notes: string[]; impact: Impact;
}

export interface Entry {
  seq: number; at: string; author: string; kind: Kind; id: string; action: string; version: number | null;
  hash_before: string | null; hash_after: string | null; note: string; source: string;
  recipe_hashes: Record<string, string>; impact: Partial<Impact>; tags: string[]; cause: string | null;
}

export interface History {
  kind: Kind; id: string; live: boolean; current_hash: string | null; archived: boolean; changed_outside: boolean;
  versions: (Entry & { tags: string[] })[];
}

export interface VersionStats { version: number | string; n: number; mean_e0: number; success: number; tokens: number;
  by: Record<string, { n: number; mean_e0: number; success: number; tokens: number }> }

export interface Card {
  kind: string; id: string; title: string; summary: string; hash?: string | null; verdict?: "READY" | "BLOCKED" | null;
  issues: string[]; markdown: string; result?: Record<string, unknown> | null; saved?: string;
  sections: { title: string; fields: [string, string][]; table?: { headers: string[]; rows: string[][] } | null; text?: string | null; note?: string | null }[];
}

export const KINDS: { kind: Kind; label: string; editable: boolean; help: string }[] = [
  { kind: "recipe", label: "Harnesses", editable: true, help: "Recipes: shape, prompts, tools, budgets, permissions. A matrix recipe = a base + one change." },
  { kind: "skill", label: "Skills", editable: true, help: "General how-tos a recipe gives the model, upfront or on demand. Their text is in every user's recipe hash." },
  { kind: "prompt", label: "Prompts", editable: true, help: "Jinja templates by layer (system, task, stage, plan, ...). Their source is in every user's recipe hash." },
  { kind: "server", label: "Tool servers", editable: true, help: "MCP servers and their tools' side effects and risks. Built-in servers are read-only here." },
  { kind: "fault", label: "Fault profiles", editable: true, help: "Tools that fail on purpose, to measure robustness R." },
  { kind: "model", label: "Models", editable: true, help: "Models per provider (providers.yaml). The provider model id is in every run's environment hash; prices are not." },
  { kind: "task", label: "Tasks", editable: false, help: "Read-only here: a task needs a reference solution and trap tests. Its history is recorded by Sync." },
];

export const kindLabel = (k: string) => KINDS.find((x) => x.kind === k)?.label ?? k;
