"use client";

import React from "react";
import { Stethoscope, AlertCircle, Pill, Clipboard, SquareActivity } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";

interface AuthCardProps {
  isRegisterMode: boolean;
  setIsRegisterMode: (v: boolean) => void;
  authError: string | null;
  authUsername: string;
  setAuthUsername: (v: string) => void;
  authPassword: string;
  setAuthPassword: (v: string) => void;
  authRole: string;
  setAuthRole: (v: string) => void;
  isAuthLoading: boolean;
  handleAuthSubmit: (e: React.FormEvent) => void;
  authInvite?: string;
  setAuthInvite?: (v: string) => void;
  registrationMode?: "invite" | "open" | "closed";
}

export default function AuthCard({
  isRegisterMode,
  setIsRegisterMode,
  authError,
  authUsername,
  setAuthUsername,
  authPassword,
  setAuthPassword,
  authRole,
  setAuthRole,
  isAuthLoading,
  handleAuthSubmit,
  authInvite = "",
  setAuthInvite,
  registrationMode = "invite",
}: AuthCardProps) {
  return (
    <div className="auth-split-layout">
      {/* Left Illustration Side */}
      <div className="auth-visual-panel">
        <div className="auth-brand-logo">
          <Stethoscope size={28} />
          <span>med<b style={{ color: "var(--sky)" }}>NAMA</b></span>
        </div>
        
        {/* Floating Icons */}
        <div className="floating-icons-container">
          <div className="float-icon float-1"><Stethoscope size={48} /></div>
          <div className="float-icon float-2"><SquareActivity size={56} /></div>
          <div className="float-icon float-3"><Pill size={44} /></div>
          <div className="float-icon float-4"><Clipboard size={50} /></div>
        </div>
      </div>

      {/* Right Form Side */}
      <div className="auth-form-panel">
        <div className="auth-form-container">
          {/* Mobile Brand Header */}
          <div className="auth-mobile-header">
            <div className="auth-mobile-logo-mark">
              <Stethoscope size={24} />
            </div>
            <span className="auth-mobile-logo-text">med<b>NAMA</b></span>
          </div>

          <AnimatePresence mode="wait">
            <motion.div
              key={isRegisterMode ? "register" : "login"}
              initial={{ opacity: 0, y: 12, filter: "blur(4px)" }}
              animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
              exit={{ opacity: 0, y: -12, filter: "blur(4px)" }}
              transition={{ duration: 0.22, ease: [0.16, 1, 0.3, 1] }}
            >
              <h1 className="auth-title-new">
                {isRegisterMode ? "Create an account" : "Welcome back!"}
              </h1>
              <p className="auth-subtitle-new">
                {isRegisterMode
                  ? "Join the premier clinical knowledge workspace."
                  : "Sign in to access your medical knowledge base."}
              </p>

              {authError && (
                <div className="auth-error" role="alert">
                  <AlertCircle size={14} />
                  {authError}
                </div>
              )}

              <form onSubmit={handleAuthSubmit} noValidate className="auth-form-fields">
                <div className="field">
                  <label className="field-label" htmlFor="auth-username">
                    Username
                  </label>
                  <input
                    id="auth-username"
                    type="text"
                    className="field-input-new"
                    placeholder="Enter your username"
                    value={authUsername}
                    onChange={(e) => setAuthUsername(e.target.value)}
                    autoComplete="username"
                    required
                  />
                </div>
                <div className="field">
                  <label className="field-label" htmlFor="auth-password">
                    Password
                  </label>
                  <input
                    id="auth-password"
                    type="password"
                    className="field-input-new"
                    placeholder="Enter your password"
                    value={authPassword}
                    onChange={(e) => setAuthPassword(e.target.value)}
                    autoComplete={isRegisterMode ? "new-password" : "current-password"}
                    required
                  />
                </div>
                {isRegisterMode && registrationMode === "invite" ? (
                  <div className="field">
                    <label className="field-label" htmlFor="auth-invite">
                      Invite code
                    </label>
                    <input
                      id="auth-invite"
                      type="text"
                      className="field-input-new"
                      placeholder="e.g. maple-4821 (ask the admin)"
                      value={authInvite}
                      onChange={(e) => setAuthInvite?.(e.target.value)}
                      autoComplete="off"
                      required
                    />
                  </div>
                ) : null}
                {isRegisterMode && registrationMode === "closed" ? (
                  <div className="auth-error" role="status" style={{ fontSize: "0.8rem" }}>
                    Sign-up is closed here. Ask the admin to create your account.
                  </div>
                ) : null}
                <button type="submit" className="btn-primary-new" disabled={isAuthLoading || (isRegisterMode && registrationMode === "closed")}>
                  {isAuthLoading
                    ? "Authenticating…"
                    : isRegisterMode
                    ? "Create account"
                    : "Sign in"}
                </button>
              </form>

              <div className="auth-switch-new">
                {isRegisterMode ? (
                  <>
                    Already have an account?{" "}
                    <button
                      className="auth-switch-btn-new"
                      type="button"
                      onClick={() => setIsRegisterMode(false)}
                    >
                      Sign in
                    </button>
                  </>
                ) : (
                  <>
                    New to medNAMA?{" "}
                    <button
                      className="auth-switch-btn-new"
                      type="button"
                      onClick={() => {
                        setIsRegisterMode(true);
                        setAuthRole("student");
                      }}
                    >
                      Create account
                    </button>
                  </>
                )}
              </div>
            </motion.div>
          </AnimatePresence>
        </div>
      </div>
    </div>
  );
}
