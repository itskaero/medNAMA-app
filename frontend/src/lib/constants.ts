// ─── Shared App Constants ───────────────────────────────────────────────────

// Defaults to empty string (same-origin relative URL) so requests route
// through Next.js reverse-proxy (/api/* -> backend:8000), avoiding CORS,
// cross-site cookie blocking, and Windows IPv6 localhost (::1) ECONNREFUSED errors.
export const API = process.env.NEXT_PUBLIC_API_URL || "";
