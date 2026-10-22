"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { register } from "@/lib/api";

export default function RegisterPage() {
  const router = useRouter();
  const [form, setForm] = useState({ full_name: "", reg_no: "", email: "", password: "" });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [k]: e.target.value });

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await register(form);
      router.push("/file");
    } catch (err: any) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="sheet" style={{ maxWidth: 480 }}>
      <h1 style={{ fontSize: "var(--s3)" }}>Create a student account</h1>
      <p className="muted">You only need an account to follow complaints filed in your name.</p>
      {error && <p className="error">{error}</p>}
      <label className="field">
        <span>Full name</span>
        <input value={form.full_name} onChange={set("full_name")} required minLength={2} />
      </label>
      <label className="field">
        <span>Registration number</span>
        <input value={form.reg_no} onChange={set("reg_no")} required minLength={3} />
      </label>
      <label className="field">
        <span>College email</span>
        <input type="email" value={form.email} onChange={set("email")} required autoComplete="username" />
      </label>
      <label className="field">
        <span>
          Password <span className="hint">at least 8 characters</span>
        </span>
        <input type="password" value={form.password} onChange={set("password")} required minLength={8} autoComplete="new-password" />
      </label>
      <button className="btn" disabled={busy}>
        {busy ? "Creating account…" : "Create account"}
      </button>
      <p className="small muted" style={{ marginTop: 18 }}>
        Already registered? <Link href="/login">Sign in</Link>.
      </p>
    </form>
  );
}
