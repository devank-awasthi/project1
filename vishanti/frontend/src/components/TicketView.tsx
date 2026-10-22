"use client";

import { useCallback, useEffect, useState } from "react";
import { API, api, getAnonToken, loadSession, type Entry, type Ticket } from "@/lib/api";
import { STATUS_LABEL, STATUS_LABEL_STAFF, remaining, short, when } from "@/lib/format";
import { useSession } from "@/lib/session";
import { useLiveEvents } from "@/lib/stream";
import { LadderView } from "./Ladder";
import { useLiveBus, useToast } from "./Live";
import { Record } from "./Record";
import styles from "./TicketView.module.css";

const PRIORITY_SCALE: Record<string, number> = { critical: 0.25, high: 0.5, medium: 1, low: 1.5 };

export function useNow(ms = 1000) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(t);
  }, [ms]);
  return now;
}

export function Clock({ ticket }: { ticket: Ticket }) {
  const now = useNow();
  if (ticket.status === "CLOSED") {
    return (
      <div className={styles.clock}>
        <span className={styles.clockMain}>Closed {when(ticket.closed_at)}</span>
        <span className="small muted">
          {ticket.breach_count === 0 ? "Handled within its deadline." : `Missed ${ticket.breach_count} deadline(s) along the way.`}
        </span>
      </div>
    );
  }
  if (ticket.status === "RESOLVED") {
    const r = remaining(ticket.confirm_by, now);
    return (
      <div className={styles.clock}>
        <span className={styles.clockMain}>{r.overdue ? "Closing automatically" : r.text.replace("left", "to confirm or dispute")}</span>
        <span className="small muted">The deadline is paused while the petitioner checks the fix.</span>
      </div>
    );
  }
  const r = remaining(ticket.due_at, now);
  const start = ticket.timer_started_at ? new Date(ticket.timer_started_at).getTime() : now;
  const end = ticket.due_at ? new Date(ticket.due_at).getTime() : now;
  const frac = Math.min(1, Math.max(0, (now - start) / Math.max(1, end - start)));
  const soon = !r.overdue && frac > 0.8;
  return (
    <div className={styles.clock}>
      <span className={styles.clockMain}>
        {ticket.ombudsperson_alerted_at ? (
          "Top authority missed its deadline"
        ) : (
          <>
            {ticket.handler}{" "}
            <span className={`clock ${r.overdue ? "overdue" : soon ? "soon" : ""}`}>
              {r.overdue ? `is ${r.text}` : `has ${r.text}`}
            </span>
          </>
        )}
      </span>
      <div className={styles.bar} aria-hidden="true">
        <div style={{ width: `${frac * 100}%` }} className={r.overdue ? styles.barBad : soon ? styles.barWarn : ""} />
      </div>
      <span className="small muted">
        Deadline {when(ticket.due_at)}.{" "}
        {r.overdue && !ticket.ombudsperson_alerted_at ? "The deadline enforcer will move it up within a minute." : "If missed, it moves up a level automatically."}
      </span>
    </div>
  );
}

async function download(ticket: Ticket, a: Ticket["attachments"][number], anon: boolean) {
  const headers: Record<string, string> = {};
  const tok = anon ? getAnonToken() : null;
  if (tok) headers["X-Anon-Token"] = tok;
  else {
    const s = loadSession();
    if (s) headers["Authorization"] = `Bearer ${s.access_token}`;
  }
  const r = await fetch(`${API}/api/tickets/${ticket.id}/attachments/${a.id}`, { headers });
  if (!r.ok) return;
  const url = URL.createObjectURL(await r.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = a.filename;
  link.click();
  URL.revokeObjectURL(url);
}

export function TicketView({ id, anon = false }: { id: string; anon?: boolean }) {
  const { user } = useSession();
  const toast = useToast();
  const [ticket, setTicket] = useState<Ticket | null>(null);
  const [entries, setEntries] = useState<Entry[]>([]);
  const [views, setViews] = useState<{ designation: string; action: string; at: string }[]>([]);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const t = anon ? await api<Ticket>("/api/anon/ticket", { anon: true }) : await api<Ticket>(`/api/tickets/${id}`);
      setTicket(t);
      const l = await api<{ entries: Entry[] }>(`/api/tickets/${t.id}/ledger`, { anon });
      setEntries(l.entries);
      const isPetitioner = anon || user?.role === "student" || user?.role === "admin";
      if (isPetitioner) setViews(await api(`/api/tickets/${t.id}/access-log`, { anon }).catch(() => []));
    } catch (e: any) {
      setError(e.message);
    }
  }, [id, anon, user?.role]);

  useEffect(() => {
    load();
  }, [load]);

  const onLive = useCallback(
    (e: { data: Record<string, any> }) => {
      if (ticket && e.data?.ticket_id === ticket.id) load();
    },
    [ticket, load],
  );
  useLiveBus(onLive);
  // Anonymous petitioners have no session, so they get their own stream.
  useLiveEvents(
    (e) => {
      onLive(e);
      if (e.data?.message) toast(e.data.message);
    },
    { anon: true, enabled: anon },
  );

  async function act(action: string, withNote = false) {
    if (!ticket) return;
    setBusy(true);
    setError("");
    try {
      await api(`/api/tickets/${ticket.id}/actions`, { body: { action, note: withNote ? note : null }, anon });
      setNote("");
      await load();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function postComment(e: React.FormEvent) {
    e.preventDefault();
    if (!ticket || !comment.trim()) return;
    setBusy(true);
    try {
      await api(`/api/tickets/${ticket.id}/comments`, { body: { text: comment }, anon });
      setComment("");
      await load();
    } catch (err: any) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  if (error && !ticket) return <p className="error">{error}</p>;
  if (!ticket) return <p className="muted">Loading the complaint…</p>;

  const staff = user?.role === "official" || user?.role === "admin";
  const petitioner = anon || (user?.role === "student" && !ticket.anonymous);
  const handlesNow = user?.role === "official" && user.designation === ticket.handler && user.level === ticket.level;
  const mine = ticket.assignee?.id === user?.id;
  const labels = staff ? STATUS_LABEL_STAFF : STATUS_LABEL;

  return (
    <div className="split">
      <div className="stack">
        <div>
          <div className="row small muted" style={{ marginBottom: 8 }}>
            <span className="mono">{ticket.ref}</span>
            <span className={`status ${ticket.status}`}>{labels[ticket.status]}</span>
            {ticket.anonymous && <span>Anonymous</span>}
          </div>
          <h1 style={{ fontSize: "var(--s3)" }}>{ticket.title}</h1>
          <p className="muted small">
            {ticket.category}, {ticket.priority} priority. Filed {when(ticket.created_at)}. Level {ticket.level} of {ticket.ladder.length}
            {ticket.assignee ? `, with the ${ticket.assignee.designation}` : ", waiting to be picked up"}.
          </p>
        </div>

        {ticket.ombudsperson_alerted_at && (
          <p className="error">
            Every authority on this path missed its deadline or the top-level resolution was disputed. The external ombudsperson
            was alerted {when(ticket.ombudsperson_alerted_at)} and the breach is listed on the public record.
          </p>
        )}

        <Clock ticket={ticket} />

        {error && <p className="error">{error}</p>}

        {handlesNow && (ticket.status === "FILED" || ticket.status === "ESCALATED") && (
          <div className="panel">
            <p>This complaint is in your queue and nobody has picked it up.</p>
            <button className="btn" disabled={busy} onClick={() => act("ASSIGN")}>
              Pick up this complaint
            </button>
          </div>
        )}
        {mine && ticket.status === "ASSIGNED" && (
          <div className="panel">
            <p>You picked this up. Start the review when you begin working on it.</p>
            <button className="btn" disabled={busy} onClick={() => act("START_REVIEW")}>
              Start review
            </button>
          </div>
        )}
        {mine && ticket.status === "IN_REVIEW" && (
          <div className="panel">
            <label className="field">
              <span>What was done</span>
              <textarea value={note} onChange={(e) => setNote(e.target.value)} style={{ minHeight: 90 }} />
            </label>
            <button className="btn ok" disabled={busy || !note.trim()} onClick={() => act("RESOLVE", true)}>
              Mark resolved
            </button>
            <p className="small muted" style={{ marginTop: 10 }}>
              The petitioner then has 3 days to confirm. A dispute sends the complaint to the next level.
            </p>
          </div>
        )}
        {petitioner && ticket.status === "RESOLVED" && (
          <div className="panel">
            <h3>Was the problem fixed?</h3>
            <p className="small muted">If you do nothing, the complaint closes on its own when the time runs out.</p>
            <div className="row" style={{ marginBottom: 14 }}>
              <button className="btn ok" disabled={busy} onClick={() => act("CONFIRM")}>
                Yes, close it
              </button>
            </div>
            <label className="field">
              <span>Not fixed? Say what is still wrong</span>
              <textarea value={note} onChange={(e) => setNote(e.target.value)} style={{ minHeight: 80 }} />
            </label>
            <button className="btn danger" disabled={busy || !note.trim()} onClick={() => act("DISPUTE", true)}>
              Dispute and send it up
            </button>
          </div>
        )}

        <section>
          <h3>Complaint</h3>
          <p style={{ whiteSpace: "pre-wrap" }}>{ticket.description}</p>
          {ticket.attachments.length > 0 && (
            <ul className={styles.files}>
              {ticket.attachments.map((a) => (
                <li key={a.id}>
                  <button className="btn secondary small" onClick={() => download(ticket, a, anon)}>
                    Download {a.filename}
                  </button>
                  <span className="small muted">
                    Fingerprint <span className="mono">{short(a.sha256, 16)}</span>, sealed when filed
                  </span>
                </li>
              ))}
            </ul>
          )}
          {staff && ticket.cluster && (
            <p className="notice small">
              {ticket.cluster.size} similar complaints are grouped with this one ({ticket.cluster.label}). This may be a
              systemic issue.
            </p>
          )}
        </section>

        <section>
          <h3>Escalation path</h3>
          <LadderView
            ladder={ticket.ladder}
            current={ticket.level}
            ombudsperson={!!ticket.ombudsperson_alerted_at}
            scale={PRIORITY_SCALE[ticket.priority] ?? 1}
          />
          <p className="small muted">Times shown for {ticket.priority} priority.</p>
        </section>

        {ticket.status !== "CLOSED" && (petitioner || staff) && (
          <form onSubmit={postComment}>
            <label className="field">
              <span>
                Add a comment <span className="hint">comments are sealed into the record and cannot be edited</span>
              </span>
              <textarea value={comment} onChange={(e) => setComment(e.target.value)} style={{ minHeight: 70 }} maxLength={2000} />
            </label>
            <button className="btn secondary" disabled={busy || !comment.trim()}>
              Add comment
            </button>
          </form>
        )}

        {petitioner && (
          <section>
            <h3>Who has opened this complaint</h3>
            {views.length === 0 ? (
              <p className="small muted">No official has opened it yet.</p>
            ) : (
              <ul className={styles.views}>
                {views.slice(0, 12).map((v, i) => (
                  <li key={i}>
                    <span>{v.designation}</span>
                    <span className="muted small">
                      {v.action === "view" ? "viewed" : v.action.replace("_", " ")} {when(v.at)}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        )}
      </div>

      <Record ticket={ticket} entries={entries} anon={anon} />
    </div>
  );
}

