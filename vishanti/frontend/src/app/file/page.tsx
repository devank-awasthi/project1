"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";
import { api, setAnonToken, uploadFile, type Meta, type Ticket } from "@/lib/api";
import { hoursLabel, short } from "@/lib/format";
import { useSession } from "@/lib/session";
import styles from "./file.module.css";

type Up = { name: string; state: "uploading" | "done" | "failed"; upload_id?: string; sha256?: string; error?: string };

const PRIORITIES: { key: string; label: string; help: string }[] = [
  { key: "low", label: "Low", help: "Inconvenient, not urgent" },
  { key: "medium", label: "Medium", help: "Needs fixing this week" },
  { key: "high", label: "High", help: "Affects daily life" },
  { key: "critical", label: "Critical", help: "Safety or health risk" },
];

function FileForm() {
  const params = useSearchParams();
  const router = useRouter();
  const { user, ready } = useSession();
  const [meta, setMeta] = useState<Meta | null>(null);
  const [category, setCategory] = useState("");
  const [priority, setPriority] = useState("medium");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [anonymous, setAnonymous] = useState(params.get("anonymous") === "1");
  const [uploads, setUploads] = useState<Up[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [token, setToken] = useState<{ value: string; ticket: Ticket } | null>(null);
  const [remember, setRemember] = useState(false);
  // One key per filing attempt: a retry after a timeout cannot create a second ticket.
  const idemKey = useMemo(() => crypto.randomUUID(), []);

  useEffect(() => {
    api<Meta>("/api/public/meta", { noAuth: true })
      .then((m) => {
        setMeta(m);
        setCategory(m.categories[0]);
      })
      .catch((e) => setError(e.message));
  }, []);

  const first = meta?.ladders[category]?.[0];
  const scale = meta?.priorities[priority] ?? 1;

  async function addFiles(files: FileList | null) {
    if (!files) return;
    const list = Array.from(files).slice(0, 5 - uploads.length);
    for (const f of list) {
      setUploads((u) => [...u, { name: f.name, state: "uploading" }]);
      try {
        const res = await uploadFile(f);
        setUploads((u) => u.map((x) => (x.name === f.name && x.state === "uploading" ? { ...x, state: "done", ...res } : x)));
      } catch (e: any) {
        setUploads((u) => u.map((x) => (x.name === f.name && x.state === "uploading" ? { ...x, state: "failed", error: e.message } : x)));
      }
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!anonymous && !user) {
      setError("Sign in to file in your name, or switch to anonymous.");
      return;
    }
    if (uploads.some((u) => u.state === "uploading")) {
      setError("Wait for the attachments to finish uploading.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const res = await api<{ ticket: Ticket; anonymous_token: string | null }>("/api/tickets", {
        body: {
          title,
          description,
          category,
          priority,
          anonymous,
          upload_ids: uploads.filter((u) => u.state === "done").map((u) => u.upload_id),
        },
        headers: { "Idempotency-Key": idemKey },
        noAuth: anonymous, // never send the session with an anonymous complaint
      });
      if (res.anonymous_token) setToken({ value: res.anonymous_token, ticket: res.ticket });
      else router.push(`/tickets/${res.ticket.id}`);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  if (token) {
    return (
      <div className={`sheet ${styles.tokenSheet}`}>
        <h1 style={{ fontSize: "var(--s3)" }}>Filed as {token.ticket.ref}. Save this token now.</h1>
        <p>
          This token is the only way to follow your complaint, confirm or dispute the resolution, and receive updates. We
          only keep a hash of it, so it cannot be shown again or recovered.
        </p>
        <div className={styles.token}>
          <code>{token.value}</code>
          <button className="btn secondary small" type="button" onClick={() => navigator.clipboard.writeText(token.value)}>
            Copy token
          </button>
        </div>
        <label className="row" style={{ margin: "18px 0" }}>
          <input type="checkbox" style={{ width: "auto" }} checked={remember} onChange={(e) => setRemember(e.target.checked)} />
          <span>Remember it on this device (skip this on a shared computer)</span>
        </label>
        <button
          className="btn"
          onClick={() => {
            setAnonToken(token.value, remember);
            router.push("/track");
          }}
        >
          I have saved it, open my complaint
        </button>
      </div>
    );
  }

  return (
    <form onSubmit={submit} className="split">
      <div className="stack">
        <div>
          <h1>File a complaint</h1>
          <p className="muted">Describe one problem per complaint. The deadline starts as soon as you submit.</p>
        </div>
        {error && <p className="error">{error}</p>}

        <fieldset className={styles.who}>
          <legend className="sr">Who is filing</legend>
          <label className={!anonymous ? styles.chosen : ""}>
            <input type="radio" name="who" checked={!anonymous} onChange={() => setAnonymous(false)} />
            <span>
              <strong>In my name</strong>
              <span className="small muted">
                {ready && user ? `As ${user.full_name}. You can follow it from your account.` : "You need to sign in."}
              </span>
            </span>
          </label>
          <label className={anonymous ? styles.chosen : ""}>
            <input type="radio" name="who" checked={anonymous} onChange={() => setAnonymous(true)} />
            <span>
              <strong>Anonymously</strong>
              <span className="small muted">Not linked to any account. You get a one-time token instead.</span>
            </span>
          </label>
        </fieldset>
        {!anonymous && ready && !user && (
          <p className="notice">
            <Link href="/login">Sign in</Link> or <Link href="/register">create an account</Link> to file in your name.
          </p>
        )}

        <label className="field">
          <span>Category</span>
          <select value={category} onChange={(e) => setCategory(e.target.value)}>
            {meta?.categories.map((c) => <option key={c}>{c}</option>)}
          </select>
        </label>

        <fieldset className={styles.priority}>
          <legend>Priority</legend>
          {PRIORITIES.map((p) => (
            <label key={p.key} className={priority === p.key ? styles.chosen : ""}>
              <input type="radio" name="priority" checked={priority === p.key} onChange={() => setPriority(p.key)} />
              <strong>{p.label}</strong>
              <span className="small muted">{p.help}</span>
            </label>
          ))}
        </fieldset>

        <label className="field">
          <span>Short title</span>
          <input value={title} onChange={(e) => setTitle(e.target.value)} required minLength={5} maxLength={200} />
        </label>
        <label className="field">
          <span>
            What happened <span className="hint">where, since when, who is affected</span>
          </span>
          <textarea value={description} onChange={(e) => setDescription(e.target.value)} required minLength={20} maxLength={5000} />
        </label>

        <div className="field">
          <span>
            Photos or documents <span className="hint">JPEG, PNG, WebP or PDF, up to 10 MB each, at most 5</span>
          </span>
          <input type="file" multiple accept="image/jpeg,image/png,image/webp,application/pdf" onChange={(e) => addFiles(e.target.files)} />
          {uploads.length > 0 && (
            <ul className={styles.uploads}>
              {uploads.map((u, i) => (
                <li key={i}>
                  <span>{u.name}</span>
                  {u.state === "uploading" && <span className="muted small">Uploading and removing location data…</span>}
                  {u.state === "done" && (
                    <span className="small">
                      Metadata removed. Fingerprint <span className="mono">{short(u.sha256, 12)}</span>
                    </span>
                  )}
                  {u.state === "failed" && <span className="small" style={{ color: "var(--seal)" }}>{u.error}</span>}
                </li>
              ))}
            </ul>
          )}
        </div>

        <button className="btn" disabled={busy || !meta}>
          {busy ? "Filing…" : anonymous ? "File anonymously" : "File complaint"}
        </button>
      </div>

      <aside className="panel">
        <h3>What happens next</h3>
        {first ? (
          <ol className={styles.next}>
            <li>
              The <strong>{first.designation}</strong> has <strong>{hoursLabel(first.sla_hours * scale)}</strong> to act on
              a {priority} priority {category.toLowerCase()} complaint.
            </li>
            <li>If they miss it, the complaint goes to the next authority automatically with a fresh deadline.</li>
            <li>When it is marked resolved you have 3 days to confirm or dispute it. A dispute sends it one level up.</li>
            <li>Every step is added to a sealed record you can verify from the complaint page.</li>
          </ol>
        ) : (
          <p className="muted">Loading the escalation rules…</p>
        )}
      </aside>
    </form>
  );
}

export default function FilePage() {
  return (
    <Suspense>
      <FileForm />
    </Suspense>
  );
}
