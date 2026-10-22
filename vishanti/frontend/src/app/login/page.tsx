"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { login, type User } from "@/lib/api";

const DEMO = process.env.NEXT_PUBLIC_DEMO !== "0";
const DEMO_ACCOUNTS = [
  ["Student", "student1@vishanti.edu"],
  ["Warden (level 1)", "warden@vishanti.edu"],
  ["Dean of Students (level 2)", "dean.of.students@vishanti.edu"],
  ["Registrar (level 3)", "registrar@vishanti.edu"],
  ["Administrator", "admin@vishanti.edu"],
];

function homeFor(u: User) {
  return u.role === "official" ? "/desk" : u.role === "admin" ? "/admin" : "/tickets";
}

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const u = await login(email, password);
      router.push(homeFor(u));
    } catch (err: any) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="split">
      <form onSubmit={submit} className="sheet" style={{ maxWidth: 440 }}>
        <h1 style={{ fontSize: "var(--s3)" }}>Sign in</h1>
        {error && <p className="error">{error}</p>}
        <label className="field">
          <span>Email</span>
          <input type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </label>
        <label className="field">
          <span>Password</span>
          <input
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </label>
        <button className="btn" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
        <p className="small muted" style={{ marginTop: 18 }}>
          New student? <Link href="/register">Create an account</Link>. You can also{" "}
          <Link href="/file?anonymous=1">file anonymously</Link> without one.
        </p>
      </form>
      {DEMO && (
        <div className="panel">
          <h3>Demo accounts</h3>
          <p className="small muted">All use the password vishanti123. Pick one to fill the form.</p>
          <table className="grid" style={{ background: "transparent" }}>
            <tbody>
              {DEMO_ACCOUNTS.map(([role, mail]) => (
                <tr key={mail}>
                  <td>{role}</td>
                  <td>
                    <button
                      type="button"
                      className="btn secondary small"
                      onClick={() => {
                        setEmail(mail);
                        setPassword("vishanti123");
                      }}
                    >
                      {mail}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
