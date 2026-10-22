"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useLiveBus, useToast } from "@/components/Live";
import { TicketTable } from "@/components/TicketTable";
import { api, type TicketSummary } from "@/lib/api";
import { ACTION_LABEL, short, when } from "@/lib/format";
import { useSession } from "@/lib/session";

type Integrity = {
  embedding_backend: string;
  overdue_now: number;
  runs: { id: number; started_at: string; duration_ms: number; tickets: number; entries: number; failures: number; checkpoints_ok: boolean; triggered_by: string }[];
  alerts: { id: number; ticket_id: string | null; ref: string | null; reason: string; detail: string; seq: number | null; at: string; acknowledged: boolean }[];
  checkpoints: { id: number; day: string; root_hash: string; prev_root: string; max_entry_id: number; tickets: number; anchor_url: string | null }[];
};
type Rule = { id: number; category: string; level: number; handler_designation: string; base_sla_hours: number };
type Rules = {
  escalation_rules: Rule[];
  transitions: { from: string; action: string; actor: string; to: string; requires_assignee: boolean; description: string }[];
  designations: Record<string, number>;
};

const REASON: Record<string, string> = {
  payload_altered: "Entry content edited",
  entry_hash_mismatch: "Entry seal broken",
  broken_link: "Entry no longer links to the previous one",
  sequence_gap: "Entry removed or reordered",
  head_mismatch: "Latest entries removed",
  state_mismatch: "Status edited outside the record",
  content_altered: "Complaint text edited",
  attachment_altered: "Attachment changed",
  missing_genesis: "First entry missing",
  column_mismatch: "Status columns edited",
  history_rewritten_before_checkpoint: "History rewritten before a published checkpoint",
  checkpoint_chain_broken: "Checkpoint chain broken",
};

function IntegrityTab() {
  const toast = useToast();
  const [data, setData] = useState<Integrity | null>(null);
  const [busy, setBusy] = useState("");
  const load = useCallback(() => api<Integrity>("/api/admin/integrity").then(setData), []);
  useEffect(() => {
    load();
  }, [load]);
  useLiveBus(load);

  async function run(path: string, label: string, done: (r: any) => string) {
    setBusy(label);
    try {
      toast(done(await api(path, { method: "POST" })));
      await load();
    } catch (e: any) {
      toast(e.message, "bad");
    } finally {
      setBusy("");
    }
  }

  if (!data) return <p className="muted">Loading…</p>;
  const open = data.alerts.filter((a) => !a.acknowledged);
  return (
    <div className="stack">
      <div className="row">
        <button className="btn" disabled={!!busy} onClick={() => run("/api/admin/integrity/verify", "verify", (r) => `Checked ${r.tickets} records, ${r.entries} entries: ${r.failures} failure(s)`)}>
          {busy === "verify" ? "Verifying…" : "Verify every record now"}
        </button>
        <button className="btn secondary" disabled={!!busy} onClick={() => run("/api/admin/integrity/checkpoint", "cp", (r) => `Checkpoint published: ${short(r.root_hash, 12)}`)}>
          Publish checkpoint now
        </button>
        <button
          className="btn secondary"
          disabled={!!busy}
          onClick={() => run("/api/admin/sweep", "sweep", (r) => `Deadline sweep: ${r.escalated} escalated, ${r.auto_closed} closed, ${r.warned} warned`)}
        >
          Run deadline sweep now
        </button>
      </div>
      <p className="small muted">
        {data.overdue_now} complaint(s) past deadline right now. Duplicate grouping uses {data.embedding_backend}. The scheduler
        verifies every record every few minutes and publishes a checkpoint daily.
      </p>

      <section>
        <h2>Tamper alerts</h2>
        {data.alerts.length === 0 ? (
          <div className="panel">No tampering detected.</div>
        ) : (
          <div className="table-wrap">
            <table className="grid">
              <thead>
                <tr>
                  <th>Complaint</th>
                  <th>What changed</th>
                  <th>Entry</th>
                  <th>Detected</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {data.alerts.map((a) => (
                  <tr key={a.id} className={a.acknowledged ? "" : "breach"}>
                    <td>{a.ticket_id ? <Link href={`/tickets/${a.ticket_id}`}>{a.ref}</Link> : "Checkpoints"}</td>
                    <td>
                      <strong style={{ color: a.acknowledged ? undefined : "var(--seal)" }}>{REASON[a.reason] ?? a.reason}</strong>
                      <div className="small muted">{a.detail}</div>
                    </td>
                    <td>{a.seq ?? ""}</td>
                    <td className="small">{when(a.at)}</td>
                    <td>
                      {!a.acknowledged && (
                        <button className="btn secondary small" onClick={() => api(`/api/admin/alerts/${a.id}/ack`, { method: "POST" }).then(load)}>
                          Acknowledge
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {open.length > 0 && <p className="small muted">Acknowledging hides the alert; the broken record stays broken and is reported again only if it changes.</p>}
      </section>

      <section>
        <h2>Checkpoints</h2>
        <p className="small muted">Each checkpoint fingerprints every record&apos;s latest seal and the previous checkpoint.</p>
        <div className="table-wrap">
          <table className="grid">
            <thead>
              <tr>
                <th>Day</th>
                <th>Root seal</th>
                <th>Records</th>
                <th>Published copy</th>
              </tr>
            </thead>
            <tbody>
              {data.checkpoints.map((c) => (
                <tr key={c.id}>
                  <td>{c.day}</td>
                  <td className="mono small" style={{ wordBreak: "break-all" }}>
                    {c.root_hash}
                    <div className="faint">after {short(c.prev_root, 12)}</div>
                  </td>
                  <td>{c.tickets}</td>
                  <td>{c.anchor_url ? <a href={c.anchor_url}>GitHub gist</a> : <span className="faint">not configured</span>}</td>
                </tr>
              ))}
              {data.checkpoints.length === 0 && (
                <tr>
                  <td colSpan={4} className="muted">
                    No checkpoint yet. Publish one to seal today&apos;s history.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section>
        <h2>Verification runs</h2>
        <div className="table-wrap">
          <table className="grid">
            <thead>
              <tr>
                <th>When</th>
                <th>Records</th>
                <th>Entries</th>
                <th>Time</th>
                <th>Result</th>
                <th>Started by</th>
              </tr>
            </thead>
            <tbody>
              {data.runs.map((r) => (
                <tr key={r.id} className={r.failures || !r.checkpoints_ok ? "breach" : ""}>
                  <td className="small">{when(r.started_at)}</td>
                  <td>{r.tickets}</td>
                  <td>{r.entries}</td>
                  <td>{r.duration_ms} ms</td>
                  <td>{r.failures || !r.checkpoints_ok ? <strong style={{ color: "var(--seal)" }}>{r.failures} broken</strong> : "All intact"}</td>
                  <td className="small muted">{r.triggered_by}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

function RulesTab() {
  const toast = useToast();
  const [data, setData] = useState<Rules | null>(null);
  const [edits, setEdits] = useState<Record<number, Partial<Rule>>>({});
  const load = useCallback(() => api<Rules>("/api/admin/rules").then(setData), []);
  useEffect(() => {
    load();
  }, [load]);
  if (!data) return <p className="muted">Loading…</p>;

  async function save(r: Rule) {
    const e = { ...r, ...edits[r.id] };
    try {
      await api("/api/admin/rules", {
        method: "PUT",
        body: { category: e.category, level: e.level, handler_designation: e.handler_designation, base_sla_hours: Number(e.base_sla_hours) },
      });
      toast(`Saved ${e.category} level ${e.level}. New deadlines use it from now on.`);
      setEdits((x) => ({ ...x, [r.id]: {} }));
      load();
    } catch (err: any) {
      toast(err.message, "bad");
    }
  }

  return (
    <div className="stack">
      <p className="muted">
        Who handles each category at each level, and how long they get at medium priority. Changes apply to deadlines that
        start after saving; running clocks are not moved. Every change is logged.
      </p>
      <div className="table-wrap">
        <table className="grid">
          <thead>
            <tr>
              <th>Category</th>
              <th>Level</th>
              <th>Handled by</th>
              <th>Hours</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {data.escalation_rules.map((r) => {
              const e = { ...r, ...edits[r.id] };
              const dirty = e.handler_designation !== r.handler_designation || Number(e.base_sla_hours) !== r.base_sla_hours;
              return (
                <tr key={r.id}>
                  <td>{r.level === 1 ? <strong>{r.category}</strong> : ""}</td>
                  <td>{r.level}</td>
                  <td>
                    <select
                      value={e.handler_designation}
                      onChange={(ev) => setEdits((x) => ({ ...x, [r.id]: { ...x[r.id], handler_designation: ev.target.value } }))}
                    >
                      {Object.entries(data.designations)
                        .filter(([, lvl]) => lvl === r.level)
                        .map(([d]) => (
                          <option key={d}>{d}</option>
                        ))}
                    </select>
                  </td>
                  <td style={{ width: 120 }}>
                    <input
                      type="number"
                      min={1}
                      value={e.base_sla_hours}
                      onChange={(ev) => setEdits((x) => ({ ...x, [r.id]: { ...x[r.id], base_sla_hours: Number(ev.target.value) } }))}
                    />
                  </td>
                  <td>
                    <button className="btn small" disabled={!dirty} onClick={() => save(r)}>
                      Save
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <h2>State machine</h2>
      <p className="muted">The only transitions the server accepts. Anything else is refused before it reaches the record.</p>
      <div className="table-wrap">
        <table className="grid">
          <thead>
            <tr>
              <th>From</th>
              <th>Action</th>
              <th>Who</th>
              <th>To</th>
              <th>Meaning</th>
            </tr>
          </thead>
          <tbody>
            {data.transitions.map((t, i) => (
              <tr key={i}>
                <td>{t.from}</td>
                <td>{ACTION_LABEL[t.action] ?? t.action}</td>
                <td>
                  {t.actor}
                  {t.requires_assignee ? ", assignee only" : ""}
                </td>
                <td>{t.to}</td>
                <td className="small muted">{t.description}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function TicketsTab() {
  const [status, setStatus] = useState("");
  const [rows, setRows] = useState<TicketSummary[] | null>(null);
  const load = useCallback(() => api<TicketSummary[]>(`/api/admin/tickets${status ? `?status=${status}` : ""}`).then(setRows), [status]);
  useEffect(() => {
    load();
  }, [load]);
  useLiveBus(load);
  return (
    <div className="stack">
      <label className="field" style={{ maxWidth: 260 }}>
        <span>Status</span>
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">All</option>
          {["FILED", "ASSIGNED", "IN_REVIEW", "RESOLVED", "ESCALATED", "CLOSED"].map((s) => (
            <option key={s}>{s}</option>
          ))}
        </select>
      </label>
      {rows && <TicketTable tickets={rows} staff empty={<p>No complaints with this status.</p>} />}
    </div>
  );
}

function OfficialsTab() {
  const toast = useToast();
  const [rows, setRows] = useState<any[]>([]);
  const [form, setForm] = useState({ email: "", password: "", full_name: "", designation: "Warden", level: 1 });
  const load = useCallback(() => api<any[]>("/api/admin/officials").then(setRows), []);
  useEffect(() => {
    load();
  }, [load]);
  async function add(e: React.FormEvent) {
    e.preventDefault();
    try {
      await api("/api/admin/officials", { body: form });
      toast(`Added ${form.full_name} as ${form.designation}`);
      setForm({ ...form, email: "", password: "", full_name: "" });
      load();
    } catch (err: any) {
      toast(err.message, "bad");
    }
  }
  return (
    <div className="split">
      <div className="table-wrap">
        <table className="grid">
          <thead>
            <tr>
              <th>Official</th>
              <th>Level</th>
              <th>Open with them</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((o) => (
              <tr key={o.id}>
                <td>
                  {o.designation}
                  <div className="small faint">{o.email}</div>
                </td>
                <td>{o.level}</td>
                <td>{o.open_assigned}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <form className="sheet" onSubmit={add}>
        <h3>Add an official</h3>
        <p className="small muted">Escalated complaints are routed to the least busy official with the right designation.</p>
        {(["full_name", "email", "password"] as const).map((k) => (
          <label className="field" key={k}>
            <span>{k === "full_name" ? "Name" : k === "email" ? "Email" : "Initial password"}</span>
            <input
              type={k === "password" ? "password" : k === "email" ? "email" : "text"}
              value={form[k]}
              onChange={(e) => setForm({ ...form, [k]: e.target.value })}
              required
              minLength={k === "password" ? 8 : 2}
            />
          </label>
        ))}
        <label className="field">
          <span>Designation</span>
          <input value={form.designation} onChange={(e) => setForm({ ...form, designation: e.target.value })} required />
        </label>
        <label className="field">
          <span>Level</span>
          <input type="number" min={1} max={5} value={form.level} onChange={(e) => setForm({ ...form, level: Number(e.target.value) })} />
        </label>
        <button className="btn">Add official</button>
      </form>
    </div>
  );
}

function AccessTab() {
  const [rows, setRows] = useState<any[]>([]);
  useEffect(() => {
    api<any[]>("/api/admin/access-logs").then(setRows);
  }, []);
  return (
    <div className="stack">
      <p className="muted">Every time staff open, list or download a complaint, and every rule change. This log is append-only.</p>
      <div className="table-wrap">
        <table className="grid">
          <thead>
            <tr>
              <th>When</th>
              <th>Who</th>
              <th>Did</th>
              <th>Complaint</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td className="small">{when(r.at)}</td>
                <td>{r.designation}</td>
                <td>
                  {r.action.replace("_", " ")}
                  {r.action === "rule_change" && (
                    <div className="small muted">
                      {r.detail.after.category} L{r.detail.after.level}: {r.detail.before?.base_sla_hours ?? "new"} h to {r.detail.after.base_sla_hours} h
                    </div>
                  )}
                </td>
                <td>{r.ticket_id ? <Link href={`/tickets/${r.ticket_id}`}>{r.detail?.ref ?? "open"}</Link> : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

const TABS = [
  ["integrity", "Integrity"],
  ["rules", "Escalation rules"],
  ["tickets", "All complaints"],
  ["officials", "Officials"],
  ["access", "Access log"],
] as const;

export default function Admin() {
  const { user, ready } = useSession();
  const [tab, setTab] = useState<(typeof TABS)[number][0]>("integrity");
  if (ready && user?.role !== "admin") return <p className="notice">This page is for administrators. <Link href="/login">Sign in</Link></p>;
  if (!user) return null;
  return (
    <div>
      <h1>Administration</h1>
      <div className="tabs" role="tablist">
        {TABS.map(([k, label]) => (
          <button key={k} role="tab" aria-selected={tab === k} onClick={() => setTab(k)}>
            {label}
          </button>
        ))}
      </div>
      {tab === "integrity" && <IntegrityTab />}
      {tab === "rules" && <RulesTab />}
      {tab === "tickets" && <TicketsTab />}
      {tab === "officials" && <OfficialsTab />}
      {tab === "access" && <AccessTab />}
    </div>
  );
}
