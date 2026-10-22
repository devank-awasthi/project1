"use client";

export const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type User = {
  id: string;
  email: string;
  full_name: string;
  role: "student" | "official" | "admin";
  level: number;
  designation: string | null;
  reg_no: string | null;
};

type Session = { user: User; access_token: string; refresh_token: string };

const SESSION_KEY = "vishanti.session";
const ANON_KEY = "vishanti.anon";

export function loadSession(): Session | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = localStorage.getItem(SESSION_KEY);
    return raw ? (JSON.parse(raw) as Session) : null;
  } catch {
    return null;
  }
}

export function saveSession(s: Session | null) {
  try {
    if (s) localStorage.setItem(SESSION_KEY, JSON.stringify(s));
    else localStorage.removeItem(SESSION_KEY);
  } catch {
    /* storage unavailable */
  }
  window.dispatchEvent(new Event("vishanti:session"));
}

/** The anonymous token lives only in this tab unless the petitioner opts in. */
export function getAnonToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return sessionStorage.getItem(ANON_KEY) ?? localStorage.getItem(ANON_KEY);
  } catch {
    return null;
  }
}

export function setAnonToken(token: string | null, remember = false) {
  try {
    sessionStorage.removeItem(ANON_KEY);
    localStorage.removeItem(ANON_KEY);
    if (token) (remember ? localStorage : sessionStorage).setItem(ANON_KEY, token);
  } catch {
    /* storage unavailable */
  }
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function refresh(): Promise<boolean> {
  const s = loadSession();
  if (!s) return false;
  const r = await fetch(`${API}/api/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: s.refresh_token }),
  });
  if (!r.ok) {
    saveSession(null);
    return false;
  }
  saveSession(await r.json());
  return true;
}

type Opts = {
  method?: string;
  body?: unknown;
  headers?: Record<string, string>;
  anon?: boolean; // authenticate with the anonymous token instead of the session
  noAuth?: boolean;
};

export async function api<T = any>(path: string, opts: Opts = {}, retried = false): Promise<T> {
  const headers: Record<string, string> = { ...(opts.headers ?? {}) };
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  if (!opts.noAuth) {
    const anon = opts.anon ? getAnonToken() : null;
    const s = loadSession();
    if (anon) headers["X-Anon-Token"] = anon;
    else if (s) headers["Authorization"] = `Bearer ${s.access_token}`;
  }
  let r: Response;
  try {
    r = await fetch(`${API}${path}`, {
      method: opts.method ?? (opts.body !== undefined ? "POST" : "GET"),
      headers,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    });
  } catch {
    throw new ApiError(0, "Can't reach the server. Check your connection and try again.");
  }
  if (r.status === 401 && !retried && !opts.anon && loadSession()) {
    if (await refresh()) return api<T>(path, opts, true);
  }
  if (!r.ok) {
    let msg = `Request failed (${r.status})`;
    try {
      const j = await r.json();
      if (typeof j.detail === "string") msg = j.detail;
      else if (Array.isArray(j.detail)) msg = j.detail.map((d: any) => d.msg).join("; ");
    } catch {
      /* not json */
    }
    throw new ApiError(r.status, msg);
  }
  return (r.status === 204 ? null : await r.json()) as T;
}

export async function login(email: string, password: string) {
  const s = await api<Session>("/api/auth/login", { body: { email, password }, noAuth: true });
  saveSession(s);
  return s.user;
}

export async function register(body: { email: string; password: string; full_name: string; reg_no: string }) {
  const s = await api<Session>("/api/auth/register", { body, noAuth: true });
  saveSession(s);
  return s.user;
}

export function logout() {
  saveSession(null);
}

// ---------------------------------------------------------------- types

export type Status = "FILED" | "ASSIGNED" | "IN_REVIEW" | "RESOLVED" | "ESCALATED" | "CLOSED";

export type Ticket = {
  id: string;
  ref: string;
  title: string;
  description: string;
  category: string;
  priority: string;
  status: Status;
  level: number;
  handler: string | null;
  ladder: { level: number; designation: string; sla_hours: number }[];
  assignee: { id: string; designation: string; name: string } | null;
  anonymous: boolean;
  due_at: string | null;
  timer_started_at: string | null;
  breach_count: number;
  ombudsperson_alerted_at: string | null;
  resolved_at: string | null;
  confirm_by: string | null;
  closed_at: string | null;
  created_at: string;
  updated_at: string;
  head_hash: string | null;
  head_seq: number;
  attachments: { id: string; filename: string; content_type: string; size: number; sha256: string }[];
  sentiment?: number | null;
  cluster?: { id: string; size: number; label: string } | null;
};

export type TicketSummary = {
  id: string;
  ref: string;
  title: string;
  category: string;
  priority: string;
  status: Status;
  level: number;
  due_at: string | null;
  breach_count: number;
  anonymous: boolean;
  assignee_id: string | null;
  cluster_id: string | null;
  ombudsperson: boolean;
  created_at: string;
  updated_at: string;
};

export type Entry = {
  seq: number;
  action: string;
  actor: string;
  from: string | null;
  to: string | null;
  payload: Record<string, any>;
  payload_hash: string;
  prev_hash: string;
  hash: string;
  ts: string;
};

export type ChainReport = {
  ticket_id: string;
  ref: string;
  ok: boolean;
  entries: number;
  head_hash: string | null;
  reason: string | null;
  failure_seq: number | null;
  detail: string | null;
  checks: string[];
};

export type Meta = {
  categories: string[];
  priorities: Record<string, number>;
  ladders: Record<string, { level: number; designation: string; sla_hours: number }[]>;
  transitions: { from: string; action: string; actor: string; to: string; description: string }[];
};

// ---------------------------------------------------------------- uploads

export async function uploadFile(file: File): Promise<{ upload_id: string; sha256: string; filename: string; size: number }> {
  const p = await api<{ upload_id: string; put_url: string; headers: Record<string, string> }>("/api/uploads", {
    body: { filename: file.name, content_type: file.type, size: file.size },
    noAuth: true,
  });
  const put = await fetch(p.put_url, { method: "PUT", body: file, headers: p.headers });
  if (!put.ok) throw new ApiError(put.status, `Upload of ${file.name} failed`);
  return api(`/api/uploads/${p.upload_id}/finalize`, { method: "POST", noAuth: true });
}
