"use client";

import { useState } from "react";
import { api, type ChainReport, type Entry, type Ticket } from "@/lib/api";
import { ACTION_LABEL, actorLabel, short, when } from "@/lib/format";
import { GENESIS_PREV, checkEntry, type LinkCheck } from "@/lib/verify";
import styles from "./Record.module.css";

const SERVER_CHECKS: Record<string, string> = {
  "hash chain intact": "Every seal recomputed on the server matches",
  "head matches": "Nothing has been removed from the end of the record",
  "state replay matches": "The current status is exactly what the record says",
  "complaint content matches": "The complaint text is unchanged since filing",
  "attachment hashes match": "Attachments are unchanged since filing",
};

const STATUS_WORD: Record<string, string> = {
  FILED: "Filed",
  ASSIGNED: "Assigned",
  IN_REVIEW: "In review",
  RESOLVED: "Resolved",
  ESCALATED: "Escalated",
  CLOSED: "Closed",
};

function Details({ e }: { e: Entry }) {
  const p = e.payload;
  return (
    <>
      {e.from && e.to && e.from !== e.to && (
        <div className="small muted">
          {STATUS_WORD[e.from]} to {STATUS_WORD[e.to]}
          {p.level ? `, level ${p.level}` : ""}
        </div>
      )}
      {e.action === "GENESIS" && (
        <div className="small muted">
          {p.category}, {p.priority} priority, {p.petitioner === "anonymous" ? "filed anonymously" : "filed with an account"}.
          {p.attachments?.length ? ` ${p.attachments.length} attachment fingerprint(s) sealed.` : ""} {p.handler} has until{" "}
          {when(p.due_at)}.
        </div>
      )}
      {e.action === "AUTO_ASSIGN" && p.assignee && <div className="small muted">Now with the {String(p.assignee).split(":")[0]}</div>}
      {p.handler && e.action !== "GENESIS" && (
        <div className="small muted">
          Handed to the {p.handler}, new deadline {when(p.due_at)}
        </div>
      )}
      {p.breached_due_at && <div className="small muted">Missed deadline: {when(p.breached_due_at)}</div>}
      {p.reason && <div className="small muted">{p.reason}</div>}
      {(p.note || p.text) && <blockquote className={styles.quote}>{p.note ?? p.text}</blockquote>}
    </>
  );
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export function Record({ ticket, entries, anon }: { ticket: Ticket; entries: Entry[]; anon: boolean }) {
  const [checks, setChecks] = useState<Record<number, LinkCheck>>({});
  const [phase, setPhase] = useState<"idle" | "checking" | "done">("idle");
  const [server, setServer] = useState<ChainReport | null>(null);
  const [err, setErr] = useState("");

  async function verify() {
    setPhase("checking");
    setChecks({});
    setServer(null);
    setErr("");
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let prev = GENESIS_PREV;
    for (let i = 0; i < entries.length; i++) {
      const res = await checkEntry(ticket.id, entries[i], prev, i);
      setChecks((c) => ({ ...c, [entries[i].seq]: res }));
      if (!res.ok) break;
      prev = entries[i].hash;
      if (!reduce) await sleep(140);
    }
    try {
      setServer(await api<ChainReport>(`/api/tickets/${ticket.id}/verify`, { anon }));
    } catch (e: any) {
      setErr(e.message);
    }
    setPhase("done");
  }

  const browserFail = Object.values(checks).find((c) => !c.ok);
  const browserOk = phase === "done" && !browserFail && Object.keys(checks).length === entries.length;
  const intact = browserOk && server?.ok;

  return (
    <section className={styles.record} aria-labelledby="record-h">
      <div className="spread">
        <h2 id="record-h">Record</h2>
        <button className="btn secondary small" onClick={verify} disabled={phase === "checking" || entries.length === 0}>
          {phase === "checking" ? "Checking…" : "Verify record"}
        </button>
      </div>
      <p className="small muted">
        {entries.length} sealed entries. Each seal is a SHA-256 fingerprint of the entry and of the seal before it, so changing
        any entry breaks every seal after it. Verifying recomputes them here in your browser, then asks the server for the
        deeper checks.
      </p>

      {phase === "done" && (
        <div className={`${styles.verdict} ${intact ? styles.good : styles.bad}`} role="status">
          {intact ? (
            <>
              <strong>Record intact.</strong>
              <ul>
                <li>{entries.length} seals recomputed in your browser and all match</li>
                {server!.checks.map((c) => (
                  <li key={c}>{SERVER_CHECKS[c] ?? c}</li>
                ))}
              </ul>
            </>
          ) : (
            <>
              <strong>Tamper detected.</strong>{" "}
              {browserFail
                ? `Entry ${browserFail.seq}: ${browserFail.problem}.`
                : server && !server.ok
                  ? `${server.detail}${server.failure_seq !== null ? ` (entry ${server.failure_seq})` : ""}.`
                  : err || "The record could not be checked."}
              <div className="small">Administrators are alerted automatically by the scheduled verifier.</div>
            </>
          )}
        </div>
      )}

      <ol className={styles.chain}>
        {entries.map((e) => {
          const c = checks[e.seq];
          return (
            <li key={e.seq} className={c ? (c.ok ? styles.ok : styles.fail) : ""}>
              <span className={styles.node} aria-hidden="true">
                {e.seq}
              </span>
              <div className={styles.body}>
                <div className={styles.link}>
                  {e.seq === 0 ? "first entry" : <>links to <code>{short(e.prev_hash, 12)}</code></>}
                </div>
                <div className="spread">
                  <strong>{ACTION_LABEL[e.action] ?? e.action}</strong>
                  <span className="small muted">{when(e.ts)}</span>
                </div>
                <div className="small">{actorLabel(e.actor)}</div>
                <Details e={e} />
                <div className={styles.seal}>
                  seal <code title={e.hash}>{short(e.hash, 12)}</code>
                  {c && !c.ok && <span className={styles.problem}>{c.problem}</span>}
                </div>
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
