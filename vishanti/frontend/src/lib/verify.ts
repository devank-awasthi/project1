import type { Entry } from "./api";

/**
 * Independent, in-browser re-computation of a ticket's hash chain.
 * Mirrors backend/app/canonical.py and ledger.compute_entry_hash exactly:
 * canonical JSON = keys sorted, no whitespace, UTF-8; then SHA-256.
 */
export function canonical(value: unknown): string {
  if (value === null || typeof value === "boolean" || typeof value === "string") return JSON.stringify(value);
  if (typeof value === "number") {
    if (!Number.isInteger(value)) throw new Error("floats are not allowed in hashed payloads");
    return String(value);
  }
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (typeof value === "object") {
    const obj = value as Record<string, unknown>;
    return `{${Object.keys(obj)
      .sort()
      .map((k) => `${JSON.stringify(k)}:${canonical(obj[k])}`)
      .join(",")}}`;
  }
  throw new Error(`unsupported value ${typeof value}`);
}

export async function sha256(text: string): Promise<string> {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return Array.from(new Uint8Array(buf), (b) => b.toString(16).padStart(2, "0")).join("");
}

export type LinkCheck = { seq: number; ok: boolean; problem?: string };

export async function checkEntry(ticketId: string, e: Entry, expectedPrev: string, expectedSeq: number): Promise<LinkCheck> {
  if (e.seq !== expectedSeq) return { seq: e.seq, ok: false, problem: `expected entry ${expectedSeq}, found ${e.seq}` };
  if (e.prev_hash !== expectedPrev) return { seq: e.seq, ok: false, problem: "does not link to the previous entry" };
  const payloadHash = await sha256(canonical(e.payload));
  if (payloadHash !== e.payload_hash) return { seq: e.seq, ok: false, problem: "content changed after it was sealed" };
  const material = {
    v: 1,
    ticket_id: ticketId,
    seq: e.seq,
    prev_hash: e.prev_hash,
    actor: e.actor,
    action: e.action,
    payload_hash: e.payload_hash,
    ts: e.ts,
  };
  const h = await sha256(canonical(material));
  if (h !== e.hash) return { seq: e.seq, ok: false, problem: "seal does not match its contents" };
  return { seq: e.seq, ok: true };
}

export const GENESIS_PREV = "0".repeat(64);
