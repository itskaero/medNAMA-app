// Same-origin calls through the Next proxy to PaedNAMA's server (medNAMA's API + /api/paeds).
// Sign-in is medNAMA's cookie session, shared with medNAMA on this machine.

/** A server error as one sentence: FastAPI sends validation errors as a list of objects. */
function errorText(status: number, detail: unknown): string {
  // the proxy answers 500/502/504 with no detail when the API is restarting or offline
  if (status >= 500 && (typeof detail !== "string" || detail === "Internal Server Error"))
    return "The PaedNAMA server is restarting or offline. Try again in a minute.";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((d) => (d && typeof d === "object" && "msg" in d ? String(d.msg) : String(d))).join("; ");
  if (status === 401) return "Please sign in again.";
  return `Something went wrong (${status}).`;
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    credentials: "include",
    headers: { ...(init?.body && !(init.body instanceof URLSearchParams) ? { "Content-Type": "application/json" } : {}), ...init?.headers },
  });
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const err = new Error(errorText(res.status, body?.detail)) as Error & { status?: number };
    err.status = res.status;
    throw err;
  }
  return body as T;
}

export const MEDNAMA_URL = process.env.NEXT_PUBLIC_MEDNAMA_URL || "http://localhost:3000";
