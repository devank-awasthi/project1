// Server-side fetches (server components). Inside Docker the API is reached by
// its service name, so this can differ from the browser-facing URL.
const INTERNAL = process.env.INTERNAL_API_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export async function serverGet<T>(path: string, revalidate = 60): Promise<T | null> {
  try {
    const r = await fetch(`${INTERNAL}${path}`, { next: { revalidate } });
    if (!r.ok) return null;
    return (await r.json()) as T;
  } catch {
    return null;
  }
}
