"use client";

import Link from "next/link";
import type { TicketSummary } from "@/lib/api";
import { STATUS_LABEL, STATUS_LABEL_STAFF, remaining, when } from "@/lib/format";
import { useNow } from "./TicketView";

export function TicketTable({
  tickets,
  staff = false,
  empty,
}: {
  tickets: TicketSummary[];
  staff?: boolean;
  empty: React.ReactNode;
}) {
  const now = useNow(15000);
  if (tickets.length === 0) return <div className="panel">{empty}</div>;
  const labels = staff ? STATUS_LABEL_STAFF : STATUS_LABEL;
  return (
    <div className="table-wrap">
      <table className="grid">
        <thead>
          <tr>
            <th>Complaint</th>
            <th>Category</th>
            <th>Status</th>
            <th>Level</th>
            <th>Deadline</th>
          </tr>
        </thead>
        <tbody>
          {tickets.map((t) => {
            const running = ["FILED", "ASSIGNED", "IN_REVIEW", "ESCALATED"].includes(t.status) && !t.ombudsperson;
            const r = remaining(t.due_at, now);
            const soon = running && !r.overdue && r.ms < 3 * 3600_000;
            return (
              <tr key={t.id} className={t.breach_count > 0 || t.ombudsperson ? "breach" : ""}>
                <td>
                  <Link href={`/tickets/${t.id}`}>{t.title}</Link>
                  <div className="small faint">
                    <span className="mono">{t.ref}</span>, filed {when(t.created_at)}
                    {t.anonymous ? ", anonymous" : ""}
                  </div>
                </td>
                <td>
                  {t.category}
                  <div className="small faint">{t.priority} priority</div>
                </td>
                <td>
                  <span className={`status ${t.status}`}>{labels[t.status]}</span>
                </td>
                <td>
                  {t.level}
                  {t.breach_count > 0 && <div className="small" style={{ color: "var(--seal)" }}>{t.breach_count} missed</div>}
                </td>
                <td>
                  {t.ombudsperson ? (
                    <span className="clock overdue">Ombudsperson alerted</span>
                  ) : running ? (
                    <span className={`clock ${r.overdue ? "overdue" : soon ? "soon" : ""}`}>{r.text}</span>
                  ) : (
                    <span className="faint">Paused</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
