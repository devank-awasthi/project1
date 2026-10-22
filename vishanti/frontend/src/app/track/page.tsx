"use client";

import { useEffect, useState } from "react";
import { TicketView } from "@/components/TicketView";
import { api, getAnonToken, setAnonToken } from "@/lib/api";

export default function TrackPage() {
  const [token, setToken] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [remember, setRemember] = useState(false);
  const [error, setError] = useState("");
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    setToken(getAnonToken());
    setChecked(true);
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    const value = input.trim();
    setAnonToken(value, remember);
    try {
      await api("/api/anon/ticket", { anon: true });
      setToken(value);
    } catch (err: any) {
      setAnonToken(null);
      setError(err.status === 404 || err.status === 401 ? "No complaint matches that token. Check it was copied in full." : err.message);
    }
  }

  if (!checked) return null;

  if (token) {
    return (
      <div className="stack">
        <div className="row small">
          <span className="muted">Signed in with an anonymous token.</span>
          <button
            className="btn secondary small"
            onClick={() => {
              setAnonToken(null);
              setToken(null);
            }}
          >
            Forget token on this device
          </button>
        </div>
        <TicketView id="" anon />
      </div>
    );
  }

  return (
    <form onSubmit={submit} className="sheet" style={{ maxWidth: 620 }}>
      <h1 style={{ fontSize: "var(--s3)" }}>Track an anonymous complaint</h1>
      <p className="muted">Paste the token you were given when you filed. It starts with vsh_.</p>
      {error && <p className="error">{error}</p>}
      <label className="field">
        <span>Token</span>
        <input className="mono" value={input} onChange={(e) => setInput(e.target.value)} required autoComplete="off" spellCheck={false} />
      </label>
      <label className="row" style={{ marginBottom: 18 }}>
        <input type="checkbox" style={{ width: "auto" }} checked={remember} onChange={(e) => setRemember(e.target.checked)} />
        <span>Remember it on this device</span>
      </label>
      <button className="btn">Open my complaint</button>
    </form>
  );
}
