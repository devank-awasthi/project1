"use client";

import { useEffect, useRef } from "react";
import { API, api } from "./api";

export type LiveEvent = { id: number; event: string; data: Record<string, any> };

const EVENTS = [
  "ticket_filed",
  "status_changed",
  "comment",
  "sla_warning",
  "ombudsperson_alert",
  "tamper_detected",
  "systemic_issue",
];

/**
 * Live notifications over SSE.
 * 1. POST /api/stream/key with our normal credentials (JWT or anonymous token)
 * 2. open EventSource with the short-lived key (no secret in the URL)
 * 3. on any drop, mint a new key and reconnect with ?last_event_id= so the
 *    server replays what we missed.
 */
export function useLiveEvents(onEvent: (e: LiveEvent) => void, opts: { anon?: boolean; enabled?: boolean } = {}) {
  const handler = useRef(onEvent);
  handler.current = onEvent;
  const enabled = opts.enabled ?? true;

  useEffect(() => {
    if (!enabled) return;
    let es: EventSource | null = null;
    let lastId = 0;
    let stopped = false;
    let retry = 1000;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const connect = async () => {
      if (stopped) return;
      try {
        const { key } = await api<{ key: string }>("/api/stream/key", { method: "POST", anon: opts.anon });
        const url = `${API}/api/stream?key=${encodeURIComponent(key)}${lastId ? `&last_event_id=${lastId}` : ""}`;
        es = new EventSource(url);
        const onMsg = (ev: MessageEvent) => {
          retry = 1000;
          const id = Number(ev.lastEventId);
          if (id) lastId = Math.max(lastId, id);
          try {
            handler.current({ id, event: ev.type, data: JSON.parse(ev.data) });
          } catch {
            /* ignore malformed */
          }
        };
        EVENTS.forEach((name) => es!.addEventListener(name, onMsg));
        es.onerror = () => {
          es?.close();
          if (!stopped) timer = setTimeout(connect, (retry = Math.min(retry * 2, 30000)));
        };
      } catch {
        if (!stopped) timer = setTimeout(connect, (retry = Math.min(retry * 2, 30000)));
      }
    };
    connect();
    return () => {
      stopped = true;
      clearTimeout(timer);
      es?.close();
    };
  }, [enabled, opts.anon]);
}
