"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useLiveBus } from "@/components/Live";
import { TicketTable } from "@/components/TicketTable";
import { api, type TicketSummary } from "@/lib/api";
import { useSession } from "@/lib/session";

type Queue = { categories: string[]; level: number; designation: string; tickets: TicketSummary[] };
type Cluster = {
  id: string;
  category: string;
  label: string;
  size: number;
  avg_sentiment: number;
  systemic: boolean;
  tickets: { id: string; ref: string; title: string; status: string }[];
};

export default function Desk() {
  const { user, ready } = useSession();
  const [queue, setQueue] = useState<Queue | null>(null);
  const [clusters, setClusters] = useState<Cluster[]>([]);
  const [tab, setTab] = useState<"queue" | "similar">("queue");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      setQueue(await api<Queue>("/api/official/queue"));
      setClusters(await api<Cluster[]>("/api/official/clusters"));
    } catch (e: any) {
      setError(e.message);
    }
  }, []);
  useEffect(() => {
    if (user?.role === "official") load();
  }, [user, load]);
  useLiveBus(load);

  if (ready && user?.role !== "official") return <p className="notice">This page is for officials. <Link href="/login">Sign in</Link></p>;
  if (!queue) return error ? <p className="error">{error}</p> : <p className="muted">Loading your desk…</p>;

  const mine = queue.tickets.filter((t) => t.assignee_id === user?.id);
  const waiting = queue.tickets.filter((t) => !t.assignee_id);

  return (
    <div className="stack">
      <div>
        <h1>{queue.designation}&apos;s desk</h1>
        <p className="muted">
          Level {queue.level} for {queue.categories.join(", ")}. Sorted by deadline. Every complaint you open is logged and
          visible to its petitioner.
        </p>
      </div>
      <div className="tabs" role="tablist">
        <button role="tab" aria-selected={tab === "queue"} onClick={() => setTab("queue")}>
          Queue ({mine.length + waiting.length})
        </button>
        <button role="tab" aria-selected={tab === "similar"} onClick={() => setTab("similar")}>
          Similar complaints ({clusters.length})
        </button>
      </div>

      {tab === "queue" ? (
        <>
          <section>
            <h2>Waiting to be picked up</h2>
            <TicketTable tickets={waiting} staff empty={<p>Nothing is waiting at your level.</p>} />
          </section>
          <section>
            <h2>With you</h2>
            <TicketTable tickets={mine} staff empty={<p>You have no open complaints assigned.</p>} />
          </section>
        </>
      ) : (
        <section className="stack">
          <p className="muted">
            Complaints whose wording is close are grouped automatically. A group of three or more is flagged as a possible
            systemic issue, which usually needs one fix rather than many.
          </p>
          {clusters.length === 0 && <div className="panel">No groups of similar complaints yet.</div>}
          {clusters.map((c) => (
            <div key={c.id} className="sheet">
              <div className="spread">
                <h3>{c.label}</h3>
                <span className={c.systemic ? "status ESCALATED" : "status FILED"}>
                  {c.size} complaints{c.systemic ? ", systemic" : ""}
                </span>
              </div>
              <p className="small muted">
                {c.category}. Average tone {c.avg_sentiment < -0.3 ? "frustrated" : c.avg_sentiment > 0.3 ? "positive" : "neutral"} (
                {c.avg_sentiment}).
              </p>
              <ul className="small" style={{ margin: 0, paddingLeft: "1.2em" }}>
                {c.tickets.map((t) => (
                  <li key={t.id}>
                    <Link href={`/tickets/${t.id}`}>{t.ref}</Link> {t.title} <span className="faint">({t.status.toLowerCase()})</span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </section>
      )}
    </div>
  );
}
