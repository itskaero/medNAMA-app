"use client";

import { PROXY_DOWN_HEADER } from "@/lib/proxyFetch";

/**
 * When the backend is down or still starting (it loads its models for a few minutes after a reboot), the
 * Next.js proxy answers /api/* with HTTP 500 and the plain-text body "Internal Server Error". Most views do
 * `await res.json()` first, so the student saw `Unexpected token 'I', "Internal S"... is not valid JSON`.
 *
 * This wraps window.fetch once, before any view fetches, and turns exactly that response into a JSON 503
 * carrying a readable `detail`, which every view already displays. Real backend errors are JSON and pass
 * through untouched. The header lets proxySafeFetch still recognise it and retry once.
 */
const MESSAGE = "medNAMA's server isn't responding. It may still be starting up; try again in a minute.";

function isApiRequest(input: RequestInfo | URL): boolean {
  const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
  try {
    const u = new URL(url, window.location.href);
    return u.origin === window.location.origin && u.pathname.startsWith("/api/");
  } catch {
    return false;
  }
}

function install() {
  if (typeof window === "undefined") return;
  const w = window as typeof window & { __mednamaFetchShim?: boolean };
  if (w.__mednamaFetchShim) return;
  w.__mednamaFetchShim = true;
  const original = window.fetch.bind(window);
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const res = await original(input, init);
    if (res.status !== 500 || !isApiRequest(input)) return res;
    if ((res.headers.get("content-type") ?? "").includes("application/json")) return res;
    const body = await res.clone().text().catch(() => "");
    if (!/internal server error/i.test(body)) return res;
    return new Response(JSON.stringify({ detail: MESSAGE }), {
      status: 503,
      statusText: "Service Unavailable",
      headers: { "content-type": "application/json", [PROXY_DOWN_HEADER]: "1" },
    });
  };
}

install();

export function ApiErrorShim() {
  return null;
}
