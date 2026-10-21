"use client";

import React, { useState } from "react";
import { Baby } from "lucide-react";
import { api, MEDNAMA_URL } from "~/lib/api";

/** Sign in with a medNAMA account (medNAMA's own login: the same session works in both apps). */
export default function Login({ onSignedIn }: { onSignedIn: (u: { username: string; role: string }) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const body = new URLSearchParams({ username, password });
      const d = await api<{ username: string; role: string }>("/api/auth/login", {
        method: "POST", body, headers: { "Content-Type": "application/x-www-form-urlencoded" },
      });
      onSignedIn(d);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not sign in.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="pn-login">
      <form onSubmit={submit}>
        <span className="pn-mark"><Baby size={18} /></span>
        <h1>Paed<i>NAMA</i></h1>
        <p>Paediatrics IMM and FCPS-II, from the textbooks. Sign in with your medNAMA account.</p>
        {error ? <div className="error-banner" role="alert">{error}</div> : null}
        <label className="field-label" htmlFor="pn-user">Username</label>
        <input id="pn-user" className="field-input-new" value={username} onChange={(e) => setUsername(e.target.value)}
          autoComplete="username" required />
        <label className="field-label" htmlFor="pn-pass">Password</label>
        <input id="pn-pass" type="password" className="field-input-new" value={password}
          onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
        <button type="submit" className="btn-primary" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
        <p>No account? Create one in <a href={MEDNAMA_URL}>medNAMA</a>.</p>
      </form>
    </div>
  );
}
