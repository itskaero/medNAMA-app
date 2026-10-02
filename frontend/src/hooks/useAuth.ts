"use client";

import { useState, useEffect } from "react";
import { toast } from "sonner";
import { API } from "@/lib/constants";

// The session is an HttpOnly cookie the page cannot read. localStorage keeps only this marker, so the rest of the
// app can still ask "signed in?"; requests carry the cookie (same-origin /api), and the backend reads the cookie
// before any Authorization header.
export const SESSION_MARKER = "cookie-session";
const isJwt = (t: string | null) => !!t && t.split(".").length === 3;

export function useAuth() {
  const [token, setToken] = useState<string | null>(null);
  const [username, setUsername] = useState<string | null>(null);
  const [role, setRole] = useState<string | null>(null);
  const [isAuthLoading, setIsAuthLoading] = useState(true);
  const [authError, setAuthError] = useState<string | null>(null);
  const [authUsername, setAuthUsername] = useState("");
  const [authPassword, setAuthPassword] = useState("");
  const [authRole, setAuthRole] = useState("student");
  const [isRegisterMode, setIsRegisterMode] = useState(false);
  const [mounted, setMounted] = useState(false);

  const handleLogout = async () => {
    try {
      await fetch(`${API}/api/auth/logout`, { method: "POST", credentials: "include" });
    } catch {}
    localStorage.removeItem("token");
    localStorage.removeItem("username");
    localStorage.removeItem("role");
    setToken(null);
    setUsername(null);
    setRole(null);
    toast.info("Logged out successfully.");
  };

  // Mount + auth restore from localStorage
  useEffect(() => {
    setMounted(true);
    const savedToken = localStorage.getItem("token");
    const savedUsername = localStorage.getItem("username");
    const savedRole = localStorage.getItem("role");

    if (savedToken && savedUsername && savedRole) {
      fetch(`${API}/api/auth/me`, {
        // An older sign-in kept the token itself here: send it once so /me can set the cookie, then forget it.
        headers: isJwt(savedToken) ? { Authorization: `Bearer ${savedToken}` } : {},
        credentials: "include",
      })
        .then((res) => {
          if (res.ok) {
            localStorage.setItem("token", SESSION_MARKER);
            setToken(SESSION_MARKER);
            setUsername(savedUsername);
            setRole(savedRole);
          } else {
            handleLogout();
          }
        })
        .catch(() => {
          setToken(isJwt(savedToken) ? savedToken : SESSION_MARKER);
          setUsername(savedUsername);
          setRole(savedRole);
        })
        .finally(() => setIsAuthLoading(false));
    } else {
      setIsAuthLoading(false);
    }
  }, []);

  // Sign-up needs an invite code on this server ("invite"), is open, or is closed (the admin creates accounts).
  const [authInvite, setAuthInvite] = useState("");
  const [registrationMode, setRegistrationMode] = useState<"invite" | "open" | "closed">("invite");
  useEffect(() => {
    fetch(`${API}/api/auth/registration`).then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (d?.mode === "open" || d?.mode === "closed" || d?.mode === "invite") setRegistrationMode(d.mode); })
      .catch(() => {});
  }, []);

  const handleAuthSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!authUsername.trim() || !authPassword.trim()) {
      setAuthError("Username and password are required.");
      return;
    }
    setAuthError(null);
    setIsAuthLoading(true);
    try {
      if (isRegisterMode) {
        const res = await fetch(`${API}/api/auth/register`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify({ username: authUsername, password: authPassword, role: authRole,
                                 invite_code: authInvite.trim() || undefined }),
        });
        if (!res.ok) {
          const d = await res.json();
          throw new Error(d.detail || "Registration failed.");
        }
        setIsRegisterMode(false);
        setAuthPassword("");
        setAuthError(null);
        toast.success("Account created. Please sign in.");
      } else {
        const params = new URLSearchParams();
        params.append("username", authUsername);
        params.append("password", authPassword);
        const res = await fetch(`${API}/api/auth/login`, {
          method: "POST",
          headers: { "Content-Type": "application/x-www-form-urlencoded" },
          credentials: "include",
          body: params,
        });
        if (!res.ok) {
          const d = await res.json();
          throw new Error(d.detail || "Incorrect credentials.");
        }
        const data = await res.json();
        localStorage.setItem("token", SESSION_MARKER);
        localStorage.setItem("username", data.username);
        localStorage.setItem("role", data.role);
        setToken(SESSION_MARKER);
        setUsername(data.username);
        setRole(data.role);
        setAuthUsername("");
        setAuthPassword("");
        toast.success(`Welcome back, ${data.username}!`);
      }
    } catch (err: any) {
      setAuthError(err.message || "Authentication error.");
    } finally {
      setIsAuthLoading(false);
    }
  };

  return {
    token,
    setToken,
    username,
    setUsername,
    role,
    setRole,
    isAuthLoading,
    authError,
    authUsername,
    setAuthUsername,
    authPassword,
    setAuthPassword,
    authRole,
    setAuthRole,
    isRegisterMode,
    setIsRegisterMode,
    authInvite,
    setAuthInvite,
    registrationMode,
    mounted,
    handleAuthSubmit,
    handleLogout,
  };
}
