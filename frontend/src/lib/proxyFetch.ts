/**
 * Fetches with a single automatic retry for one specific failure mode:
 * the Next.js standalone proxy holding a pooled keep-alive socket to the
 * backend that uvicorn already closed. Such a request dies at the connection
 * layer — it never reaches the backend — and surfaces to the browser as either:
 *
 *   1. a fetch() network error ("socket hang up" / "Failed to fetch"), or
 *   2. HTTP 500 with a plain-text "Internal Server Error" body (Next.js's
 *      proxy-failure response), which breaks callers doing res.json().
 *
 * A dead socket fails almost immediately, so only failures within
 * FAST_FAILURE_MS are replayed. A failure after a long wait means the proxy
 * gave up on a request the backend was still processing (e.g. the AI call
 * took longer than the proxy timeout); replaying that would start a second
 * full run — double AI cost and, for quiz generation, a duplicate set.
 * Client-initiated aborts (AbortError) are never retried.
 */
const FAST_FAILURE_MS = 2500;

export async function proxySafeFetch(url: string, init: RequestInit): Promise<Response> {
  const isAbort = (e: unknown) => e instanceof DOMException && e.name === "AbortError";

  for (let attempt = 1; attempt <= 2; attempt++) {
    const started = Date.now();
    const canRetry = () => attempt === 1 && Date.now() - started < FAST_FAILURE_MS;
    let res: Response;
    try {
      res = await fetch(url, init);
    } catch (err) {
      if (!isAbort(err) && canRetry()) continue; // dead pooled socket -> retry once
      throw err;
    }

    if (res.ok) return res;

    // HTTP 500 + non-JSON body is the proxy's dead-socket failure signature.
    const isProxyError500 =
      res.status === 500 &&
      !(res.headers.get("content-type") ?? "").includes("application/json");
    if (isProxyError500 && canRetry()) {
      const body = await res.clone().text();
      if (/internal server error/i.test(body)) continue;
    }

    return res;
  }

  throw new Error("Request failed after retry.");
}
