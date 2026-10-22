import { serverGet } from "@/lib/server";
import styles from "./record.module.css";

export const dynamic = "force-dynamic";

type Dash = {
  generated_at: string;
  suppression_min: number;
  months: string[];
  totals: { filed: number; open: number; resolved_or_closed: number };
  open_by_level: Record<string, number | null>;
  heatmap: { category: string; cells: (number | null)[] }[];
  velocity: { category: string; resolved: number | null; median_hours: number | null }[];
  sla_compliance: { category: string; finished: number | null; sla_met_pct: number | null }[];
  ombudsperson_flags: { ref: string; category: string; since: string }[];
  systemic_issues: { category: string; size: number; mood: number }[];
  checkpoints: { day: string; root_hash: string; tickets: number; anchor_url: string | null }[];
};

const monthName = (m: string) =>
  new Date(`${m}-01T00:00:00Z`).toLocaleDateString("en-IN", { month: "short", year: "2-digit", timeZone: "UTC" });

const fmtTime = (iso: string) =>
  new Date(iso).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "Asia/Kolkata" });

function Hidden() {
  return (
    <span className={styles.hidden} title="Hidden: fewer than 5 cases, or would reveal a hidden cell">
      hidden
    </span>
  );
}

export default async function PublicRecord() {
  const d = await serverGet<Dash>("/api/public/dashboard");
  if (!d) return <p className="notice">The server is starting up. Refresh this page in about a minute.</p>;
  const max = Math.max(1, ...d.heatmap.flatMap((r) => r.cells.map((c) => c ?? 0)));

  return (
    <div className="stack">
      <div>
        <h1>Public record</h1>
        <p className="muted">
          How the institution is handling complaints, updated {fmtTime(d.generated_at)}. Any number below {d.suppression_min} is
          hidden so that no one can be identified from it.
        </p>
      </div>

      <p className={styles.totals}>
        <span>
          <strong>{d.totals.filed}</strong> complaints filed
        </span>
        <span>
          <strong>{d.totals.open}</strong> open now
        </span>
        <span>
          <strong>{d.totals.resolved_or_closed}</strong> resolved or closed
        </span>
      </p>

      {d.ombudsperson_flags.length > 0 && (
        <section className={styles.flags}>
          <h2>Deadlines missed by every authority</h2>
          <p className="small">
            These complaints reached the top of their escalation path and the top authority missed its deadline too, or the
            petitioner disputed its resolution. The external ombudsperson has been alerted.
          </p>
          <ul>
            {d.ombudsperson_flags.map((f) => (
              <li key={f.ref}>
                <span className="mono">{f.ref}</span> {f.category}, since {fmtTime(f.since)}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section>
        <h2>Complaints filed per month</h2>
        <div className="table-wrap">
          <table className={`grid ${styles.heat}`}>
            <thead>
              <tr>
                <th>Category</th>
                {d.months.map((m) => (
                  <th key={m}>{monthName(m)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {d.heatmap.map((r) => (
                <tr key={r.category}>
                  <th scope="row">{r.category}</th>
                  {r.cells.map((c, i) => (
                    <td key={i} style={c ? { background: `rgba(27, 42, 58, ${0.06 + (0.55 * c) / max})`, color: c / max > 0.55 ? "#fff" : undefined } : undefined}>
                      {c === null ? <Hidden /> : c}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <div className={styles.twoUp}>
        <section>
          <h2>Finished within deadline</h2>
          <p className="small muted">Share of resolved or closed complaints that never missed a deadline.</p>
          <ul className={styles.bars}>
            {d.sla_compliance.map((r) => (
              <li key={r.category}>
                <span>{r.category}</span>
                {r.sla_met_pct === null ? (
                  <Hidden />
                ) : (
                  <span className={styles.barRow}>
                    <span className={styles.bar}>
                      <span style={{ width: `${r.sla_met_pct}%` }} className={r.sla_met_pct < 70 ? styles.low : ""} />
                    </span>
                    <em>
                      {r.sla_met_pct}% of {r.finished}
                    </em>
                  </span>
                )}
              </li>
            ))}
          </ul>
        </section>
        <section>
          <h2>Time to resolution</h2>
          <p className="small muted">Median time from filing to resolution, last 90 days.</p>
          <div className="table-wrap">
            <table className="grid">
              <thead>
                <tr>
                  <th>Category</th>
                  <th>Resolved</th>
                  <th>Median</th>
                </tr>
              </thead>
              <tbody>
                {d.velocity.map((v) => (
                  <tr key={v.category}>
                    <td>{v.category}</td>
                    <td>{v.resolved ?? <Hidden />}</td>
                    <td>{v.median_hours === null ? <Hidden /> : v.median_hours < 48 ? `${v.median_hours} h` : `${(v.median_hours / 24).toFixed(1)} days`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="small muted" style={{ marginTop: 16 }}>
            Open complaints by level:{" "}
            {Object.entries(d.open_by_level)
              .map(([k, v]) => `${k.replace("L", "level ")}: ${v ?? "hidden"}`)
              .join(", ") || "none"}
            .
          </p>
        </section>
      </div>

      {d.systemic_issues.length > 0 && (
        <section>
          <h2>Recurring problems</h2>
          <p className="small muted">Groups of complaints with closely matching descriptions. Only the category and size are shown.</p>
          <ul className={styles.bars}>
            {d.systemic_issues.map((s, i) => (
              <li key={i}>
                <span>{s.category}</span>
                <span>{s.size} similar complaints</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section>
        <h2>Daily seals</h2>
        <p className="small muted">
          Each seal is a SHA-256 fingerprint of every complaint record and of the previous seal. If any past entry were rewritten,
          the recomputed seal would no longer match the one published here.
        </p>
        {d.checkpoints.length === 0 ? (
          <div className="panel">No seal has been published yet.</div>
        ) : (
          <ol className={styles.seals}>
            {d.checkpoints.map((c) => (
              <li key={c.root_hash}>
                <span>{c.day}</span>
                <code>{c.root_hash}</code>
                <span className="small muted">
                  {c.tickets} records{c.anchor_url ? ", " : ""}
                  {c.anchor_url && <a href={c.anchor_url}>published copy</a>}
                </span>
              </li>
            ))}
          </ol>
        )}
      </section>
    </div>
  );
}
