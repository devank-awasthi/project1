import type { Status } from "./api";

export const STATUS_LABEL: Record<Status, string> = {
  FILED: "Filed",
  ASSIGNED: "Assigned",
  IN_REVIEW: "In review",
  RESOLVED: "Awaiting your confirmation",
  ESCALATED: "Escalated",
  CLOSED: "Closed",
};

export const STATUS_LABEL_STAFF: Record<Status, string> = { ...STATUS_LABEL, RESOLVED: "Resolved, awaiting petitioner" };

export const ACTION_LABEL: Record<string, string> = {
  GENESIS: "Complaint filed",
  ASSIGN: "Picked up",
  AUTO_ASSIGN: "Routed to the next authority",
  START_REVIEW: "Review started",
  RESOLVE: "Marked resolved",
  CONFIRM: "Resolution confirmed",
  DISPUTE: "Resolution disputed",
  AUTO_CLOSE: "Closed after no response",
  SLA_ESCALATE: "Deadline missed, escalated",
  COMMENT: "Comment",
  OMBUDSPERSON_ALERT: "Ombudsperson alerted",
};

export function actorLabel(actor: string): string {
  if (actor === "petitioner:anonymous") return "Anonymous petitioner";
  if (actor.startsWith("student:")) return "Petitioner";
  if (actor.startsWith("official:")) return actor.split(":")[1];
  if (actor === "system:sla-worker") return "Deadline enforcer";
  if (actor === "system:router") return "Automatic routing";
  if (actor === "system:closer") return "Automatic closing";
  if (actor.startsWith("admin:")) return "Administrator";
  return actor;
}

export function short(hash: string | null | undefined, n = 10) {
  return hash ? hash.slice(0, n) : "";
}

const fmt = new Intl.DateTimeFormat("en-IN", {
  day: "numeric",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  timeZone: "Asia/Kolkata",
});
const fmtDay = new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", year: "numeric", timeZone: "Asia/Kolkata" });

export const when = (iso: string | null | undefined) => (iso ? fmt.format(new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z")) : "");
export const day = (iso: string) => fmtDay.format(new Date(iso));

/** "3 h 12 min left" / "overdue by 20 min" */
export function remaining(dueIso: string | null, now: number): { text: string; overdue: boolean; ms: number } {
  if (!dueIso) return { text: "No deadline running", overdue: false, ms: Infinity };
  const ms = new Date(dueIso).getTime() - now;
  const abs = Math.abs(ms);
  const d = Math.floor(abs / 86400000);
  const h = Math.floor((abs % 86400000) / 3600000);
  const m = Math.floor((abs % 3600000) / 60000);
  const s = Math.floor((abs % 60000) / 1000);
  const parts = d ? `${d} d ${h} h` : h ? `${h} h ${m} min` : m ? `${m} min ${s} s` : `${s} s`;
  return ms >= 0 ? { text: `${parts} left`, overdue: false, ms } : { text: `overdue by ${parts}`, overdue: true, ms };
}

export function hoursLabel(h: number) {
  if (h < 1) return `${Math.round(h * 60)} min`;
  const r = Math.round(h * 10) / 10;
  return r % 24 === 0 ? `${r / 24} day${r === 24 ? "" : "s"}` : `${r} h`;
}
