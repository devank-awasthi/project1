"use client";

import { createContext, useContext, useEffect, useState } from "react";
import { loadSession, type User } from "./api";

const Ctx = createContext<{ user: User | null; ready: boolean }>({ user: null, ready: false });

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);
  useEffect(() => {
    const sync = () => setUser(loadSession()?.user ?? null);
    sync();
    setReady(true);
    window.addEventListener("vishanti:session", sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener("vishanti:session", sync);
      window.removeEventListener("storage", sync);
    };
  }, []);
  return <Ctx.Provider value={{ user, ready }}>{children}</Ctx.Provider>;
}

export const useSession = () => useContext(Ctx);
