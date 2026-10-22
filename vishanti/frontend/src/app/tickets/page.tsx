"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useLiveBus } from "@/components/Live";
import { TicketTable } from "@/components/TicketTable";
import { api, type TicketSummary } from "@/lib/api";
import { useSession } from "@/lib/session";

export default function MyTickets() {
  const { user, ready } = useSession();
  const [tickets, setTickets] = useState<TicketSummary[] | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    api<TicketSummary[]>("/api/tickets/mine")
      .then(setTickets)
      .catch((e) => setError(e.message));
  }, []);
  useEffect(() => {
    if (user?.role === "student") load();
  }, [user, load]);
  useLiveBus(load);

  if (ready && !user) return <p className="notice">Sign in to see complaints filed in your name. <Link href="/login">Sign in</Link></p>;

  return (
    <div className="stack">
      <div className="spread">
        <h1>My complaints</h1>
        <Link className="btn" href="/file">
          File a complaint
        </Link>
      </div>
      <p className="muted">Complaints you filed anonymously are not listed here. Track those with their token.</p>
      {error && <p className="error">{error}</p>}
      {tickets && (
        <TicketTable
          tickets={tickets}
          empty={
            <p>
              You have not filed anything yet. <Link href="/file">File your first complaint</Link>.
            </p>
          }
        />
      )}
    </div>
  );
}
