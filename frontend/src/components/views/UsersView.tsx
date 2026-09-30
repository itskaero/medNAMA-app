"use client";

import React, { useCallback, useEffect, useState } from "react";
import { Copy, KeyRound, Loader2, Plus, Trash2, Users } from "lucide-react";
import { toast } from "sonner";
import { API } from "@/lib/constants";

interface UserRow {
  id: number; username: string; role: string; is_active: boolean; created_at: string;
  invited_with: string | null; answered: number; last_active: string | null;
}
interface Invite {
  id: number; code: string; label: string | null; uses: number; max_uses: number;
  expires_at: string | null; created_at: string; usable: boolean;
}

const day = (s: string | null) => (s ? new Date(s).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "never");

async function call(getHeaders: () => HeadersInit, path: string, init: RequestInit = {}) {
  const res = await fetch(`${API}${path}`, {
    ...init, credentials: "include",
    headers: { ...getHeaders(), ...(init.body ? { "Content-Type": "application/json" } : {}) },
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `Request failed (${res.status}).`);
  return data;
}

async function copy(text: string) {
  try { await navigator.clipboard.writeText(text); toast.success("Copied"); } catch { toast.message(text); }
}

/** Admin: who has an account, invite codes, password resets. */
export default function UsersView({ getHeaders, username }: { getHeaders: () => HeadersInit; username: string | null }) {
  const [users, setUsers] = useState<UserRow[] | null>(null);
  const [invites, setInvites] = useState<Invite[]>([]);
  const [label, setLabel] = useState("");
  const [uses, setUses] = useState(5);
  const [days, setDays] = useState(30);
  const [busy, setBusy] = useState(false);
  const [temp, setTemp] = useState<{ username: string; password: string } | null>(null);

  const load = useCallback(async () => {
    try {
      const [u, i] = await Promise.all([call(getHeaders, "/api/admin/users"), call(getHeaders, "/api/admin/invites")]);
      setUsers(u);
      setInvites(i);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Couldn't load users.");
    }
  }, [getHeaders]);
  useEffect(() => { load(); }, [load]);

  const patch = async (u: UserRow, body: Record<string, unknown>) => {
    try { await call(getHeaders, `/api/admin/users/${u.id}`, { method: "PATCH", body: JSON.stringify(body) }); await load(); }
    catch (e) { toast.error(e instanceof Error ? e.message : "Failed."); }
  };
  const reset = async (u: UserRow) => {
    try {
      const d = await call(getHeaders, `/api/admin/users/${u.id}/reset-password`, { method: "POST" });
      setTemp({ username: d.username, password: d.temporary_password });
    } catch (e) { toast.error(e instanceof Error ? e.message : "Failed."); }
  };
  const createInvite = async () => {
    setBusy(true);
    try {
      const d = await call(getHeaders, "/api/admin/invites", {
        method: "POST", body: JSON.stringify({ label: label || null, max_uses: uses, days: days || null }),
      });
      toast.success(`Invite code ${d.code} created`);
      setLabel("");
      await load();
    } catch (e) { toast.error(e instanceof Error ? e.message : "Failed."); }
    finally { setBusy(false); }
  };
  const removeInvite = async (i: Invite) => {
    try { await call(getHeaders, `/api/admin/invites/${i.id}`, { method: "DELETE" }); await load(); }
    catch (e) { toast.error(e instanceof Error ? e.message : "Failed."); }
  };

  return (
    <div className="dashboard-view" role="region" aria-label="Users" style={{ maxWidth: "1000px", margin: "0 auto", display: "flex", flexDirection: "column", gap: "var(--sp-5)" }}>
      <div>
        <h1 className="dashboard-title" style={{ display: "flex", alignItems: "center", gap: "10px" }}><Users size={24} /> Users</h1>
        <p style={{ color: "var(--text-secondary)", fontSize: "0.86rem" }}>
          Who can sign in, the invite codes new students sign up with, and password resets.
        </p>
      </div>

      {temp ? (
        <div className="workspace-card" role="status" style={{ padding: "14px 16px", border: "1px solid var(--sky)", borderRadius: "12px" }}>
          Temporary password for <b>{temp.username}</b>: <code style={{ fontSize: "0.95rem" }}>{temp.password}</code>{" "}
          <button className="btn-workspace" onClick={() => copy(temp.password)} style={{ padding: "2px 8px" }}><Copy size={12} /> Copy</button>
          <div style={{ fontSize: "0.76rem", color: "var(--text-muted)", marginTop: "4px" }}>
            Shown once. Give it to them; they can change it from the sidebar after signing in.
          </div>
        </div>
      ) : null}

      <section style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
        <h2 style={{ fontSize: "1.05rem", fontWeight: 700 }}>Invite codes</h2>
        <div style={{ display: "flex", gap: "8px", flexWrap: "wrap", alignItems: "flex-end" }}>
          <label style={{ display: "flex", flexDirection: "column", gap: "3px", fontSize: "0.74rem", color: "var(--text-secondary)" }}>
            For (optional)
            <input id="invite-label" className="field-input-new" value={label} onChange={(e) => setLabel(e.target.value)} placeholder="e.g. batch of Sept friends" style={{ minWidth: "220px" }} />
          </label>
          <label style={{ display: "flex", flexDirection: "column", gap: "3px", fontSize: "0.74rem", color: "var(--text-secondary)" }}>
            Uses
            <input id="invite-uses" type="number" min={1} max={500} className="field-input-new" value={uses} onChange={(e) => setUses(Math.max(1, Number(e.target.value) || 1))} style={{ width: "90px" }} />
          </label>
          <label style={{ display: "flex", flexDirection: "column", gap: "3px", fontSize: "0.74rem", color: "var(--text-secondary)" }}>
            Valid for (days, 0 = no expiry)
            <input id="invite-days" type="number" min={0} max={365} className="field-input-new" value={days} onChange={(e) => setDays(Math.max(0, Number(e.target.value) || 0))} style={{ width: "110px" }} />
          </label>
          <button className="btn-primary" onClick={createInvite} disabled={busy} style={{ padding: "9px 16px" }}>
            {busy ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}&nbsp;New code
          </button>
        </div>
        {invites.length ? (
          <div style={{ overflowX: "auto" }}>
            <table className="users-table">
              <thead><tr><th>Code</th><th>For</th><th>Used</th><th>Expires</th><th /></tr></thead>
              <tbody>
                {invites.map((i) => (
                  <tr key={i.id} style={{ opacity: i.usable ? 1 : 0.55 }}>
                    <td><code>{i.code}</code> <button className="btn-workspace" onClick={() => copy(i.code)} style={{ padding: "1px 6px" }} aria-label={`Copy ${i.code}`}><Copy size={11} /></button></td>
                    <td>{i.label || "—"}</td>
                    <td>{i.uses} / {i.max_uses}</td>
                    <td>{i.expires_at ? day(i.expires_at) : "no expiry"}{i.usable ? "" : " · not usable"}</td>
                    <td><button className="btn-workspace" onClick={() => removeInvite(i)} aria-label={`Delete ${i.code}`} style={{ padding: "2px 8px" }}><Trash2 size={12} /></button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <div style={{ fontSize: "0.8rem", color: "var(--text-muted)" }}>No codes yet. New students need one to sign up.</div>}
      </section>

      <section style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
        <h2 style={{ fontSize: "1.05rem", fontWeight: 700 }}>Accounts</h2>
        {!users ? <div style={{ color: "var(--text-muted)" }}><Loader2 size={14} className="animate-spin" /> Loading…</div> : (
          <div style={{ overflowX: "auto" }}>
            <table className="users-table">
              <thead><tr><th>User</th><th>Role</th><th>Joined</th><th>Last active</th><th>Answered</th><th>Status</th><th /></tr></thead>
              <tbody>
                {users.map((u) => {
                  const me = u.username === username;
                  return (
                    <tr key={u.id} style={{ opacity: u.is_active ? 1 : 0.55 }}>
                      <td><b>{u.username}</b>{u.invited_with ? <div style={{ fontSize: "0.7rem", color: "var(--text-muted)" }}>code {u.invited_with}</div> : null}</td>
                      <td>
                        <select value={u.role} disabled={me} onChange={(e) => patch(u, { role: e.target.value })} aria-label={`Role of ${u.username}`}>
                          <option value="student">student</option><option value="admin">admin</option>
                        </select>
                      </td>
                      <td>{day(u.created_at)}</td>
                      <td>{day(u.last_active)}</td>
                      <td style={{ fontVariantNumeric: "tabular-nums" }}>{u.answered.toLocaleString()}</td>
                      <td>
                        <button className="btn-workspace" disabled={me} onClick={() => patch(u, { is_active: !u.is_active })} style={{ padding: "2px 8px" }}>
                          {u.is_active ? "Active · disable" : "Disabled · enable"}
                        </button>
                      </td>
                      <td>
                        <button className="btn-workspace" onClick={() => reset(u)} style={{ padding: "2px 8px" }} title="Set a one-time temporary password">
                          <KeyRound size={12} /> Reset password
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
