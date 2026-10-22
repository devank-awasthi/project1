"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { useSession } from "@/lib/session";
import { useLiveEvents, type LiveEvent } from "@/lib/stream";

type Toast = { id: number; text: string; tone: "" | "warn" | "bad" };
const ToastCtx = createContext<(text: string, tone?: Toast["tone"]) => void>(() => {});
export const useToast = () => useContext(ToastCtx);

const BUS = "vishanti:live";

/** Subscribe to the app-wide live feed (one SSE connection per tab). */
export function useLiveBus(fn: (e: LiveEvent) => void) {
  useEffect(() => {
    const h = (ev: Event) => fn((ev as CustomEvent<LiveEvent>).detail);
    window.addEventListener(BUS, h);
    return () => window.removeEventListener(BUS, h);
  }, [fn]);
}

export function toneFor(event: string): Toast["tone"] {
  if (event === "sla_warning" || event === "systemic_issue") return "warn";
  if (event === "tamper_detected" || event === "ombudsperson_alert") return "bad";
  return "";
}

export function LiveProvider({ children }: { children: React.ReactNode }) {
  const { user } = useSession();
  const [toasts, setToasts] = useState<Toast[]>([]);

  const push = useCallback((text: string, tone: Toast["tone"] = "") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t.slice(-3), { id, text, tone }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 7000);
  }, []);

  useLiveEvents(
    (e) => {
      window.dispatchEvent(new CustomEvent(BUS, { detail: e }));
      if (e.data?.message) push(e.data.message, toneFor(e.event));
    },
    { enabled: !!user },
  );

  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.tone}`}>
            {t.text}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}
