// Server-side fetches (server components). Inside Docker the API is reached by
// its service name, so this can differ from the browser-facing URL.
const INTERNAL = process.env.INTERNAL_API_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export async function serverGet<T>(path: string): Promise<T | null> {
  try {
    // A free-tier backend may be asleep; don't hang the page waiting for it.
    const r = await fetch(`${INTERNAL}${path}`, { cache: "no-store", signal: AbortSignal.timeout(8000) });
    if (!r.ok) return null;
    return (await r.json()) as T;
  } catch {
    return null;
  }
}
