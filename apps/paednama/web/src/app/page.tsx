"use client";

import React, { useCallback, useEffect, useState } from "react";
import { Baby, ClipboardList, ExternalLink, LayoutDashboard, Library, ListTree, LogOut, MessageCircleHeart, Settings2 } from "lucide-react";
import { api, MEDNAMA_URL } from "~/lib/api";
import { daysTo } from "~/lib/dates";
import type { Profile, StageId, StageSummary, View } from "~/lib/types";
import Login from "~/components/Login";
import Setup from "~/components/Setup";
import HomeView from "~/components/HomeView";
import ExamView from "~/components/ExamView";
import SyllabusView from "~/components/SyllabusView";
import LibraryView from "~/components/LibraryView";
import TopicSearch from "~/components/TopicSearch";
import TopicView from "~/components/TopicView";
import ShifaChat from "~/components/ShifaChat";

const NAV: { view: View; label: string; short: string; icon: React.ReactNode }[] = [
  { view: "home", label: "Dashboard", short: "Today", icon: <LayoutDashboard size={16} /> },
  { view: "exam", label: "The exam", short: "Exam", icon: <ClipboardList size={16} /> },
  { view: "syllabus", label: "Syllabus", short: "Syllabus", icon: <ListTree size={16} /> },
  { view: "shifa", label: "Dr. Shifa", short: "Shifa", icon: <MessageCircleHeart size={16} /> },
  { view: "library", label: "Library", short: "Library", icon: <Library size={16} /> },
];
const VIEWS: View[] = [...NAV.map((n) => n.view), "topic"];
const viewFromHash = (): View => {
  const v = (typeof window !== "undefined" ? window.location.hash.replace(/^#\/?/, "").split("?")[0] : "") as View;
  return VIEWS.includes(v) ? v : "home";
};
const topicFromHash = () =>
  typeof window !== "undefined" ? new URLSearchParams(window.location.hash.split("?")[1] || "").get("id") : null;

export default function PaedNAMA() {
  const [user, setUser] = useState<{ username: string; role: string } | null>(null);
  const [checked, setChecked] = useState(false);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [exams, setExams] = useState<StageSummary[]>([]);
  const [setupOpen, setSetupOpen] = useState(false);
  const [view, setView] = useState<View>("home");
  const [stage, setStage] = useState<StageId>("imm");
  const [syllabusQ, setSyllabusQ] = useState("");
  const [topicId, setTopicId] = useState<string | null>(null);

  // The medNAMA session (a cookie on this machine) signs you in here too.
  useEffect(() => {
    api<{ username: string; role: string }>("/api/auth/me")
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setChecked(true));
    const sync = () => { setView(viewFromHash()); setTopicId(topicFromHash()); };
    sync();
    const onHash = sync;
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const loadProfile = useCallback(() => {
    api<Profile>("/api/paeds/profile").then((p) => {
      setProfile(p);
      if (p.stage) setStage(p.stage);
      if (p.needs_setup) setSetupOpen(true);
    }).catch(() => {});
    api<{ stages: StageSummary[] }>("/api/paeds/exams").then((d) => setExams(d.stages)).catch(() => {});
  }, []);
  useEffect(() => { if (user) loadProfile(); }, [user, loadProfile]);

  const go = (v: View) => { window.location.hash = `/${v}`; };
  const openTopic = (id: string) => { window.location.hash = `/topic?id=${encodeURIComponent(id)}`; };
  const signOut = async () => {
    await api("/api/auth/logout", { method: "POST" }).catch(() => {});
    setUser(null);
  };

  if (!checked) return <div className="init-screen"><div className="init-spinner" role="status" aria-label="Loading" /></div>;
  if (!user) return <Login onSignedIn={setUser} />;

  const days = daysTo(profile?.exam_date);
  const stageLabel = stage === "fcps2" ? "FCPS-II" : "IMM";
  const initials = user.username.slice(0, 2).toUpperCase();

  return (
    <div className="pn-shell">
      <aside className="pn-side" aria-label="Navigation">
        <div className="pn-brand">
          <span className="pn-mark"><Baby size={18} /></span>
          <span>Paed<i>NAMA</i><small>Paediatrics · IMM · FCPS-II</small></span>
        </div>
        <nav className="pn-nav" aria-label="Main">
          {NAV.map((n) => (
            <button key={n.view} type="button" className={view === n.view ? "active" : ""} aria-current={view === n.view ? "page" : undefined}
              onClick={() => go(n.view)}>
              {n.icon}{n.label}
            </button>
          ))}
        </nav>
        <div className="pn-navlabel">You</div>
        <nav className="pn-nav" aria-label="Account">
          <button type="button" onClick={() => setSetupOpen(true)}><Settings2 size={16} />My preparation</button>
          <button type="button" onClick={() => window.open(MEDNAMA_URL, "_blank", "noopener")}><ExternalLink size={16} />Open medNAMA</button>
        </nav>
        <div className="pn-countdown">
          <span className="k">{stageLabel}{profile?.rotation ? ` · ${profile.rotation}` : ""}</span>
          {days !== null ? (
            <><b className={days <= 30 ? "urgent" : undefined}>{days}</b><span className="k">days to your exam</span></>
          ) : <span className="k">Add your exam date to count down.</span>}
          <button type="button" onClick={() => setSetupOpen(true)}>{days !== null ? "Change date" : "Add date"}</button>
        </div>
        <div className="pn-side-foot">
          <span>@{user.username}</span>
          <button type="button" className="btn-logout" onClick={signOut}><LogOut size={12} /> Sign out</button>
        </div>
      </aside>

      <div className="pn-body">
        <header className="pn-topbar">
          <TopicSearch stage={stage} onPick={(q) => { setSyllabusQ(q); go("syllabus"); }} onOpen={openTopic} />
          <div className="pn-pills">
            <span className="pn-pill hide-sm">{stageLabel}</span>
            {days !== null ? <span className="pn-pill hide-sm"><em>{days}</em> days</span> : null}
            <button type="button" className="pn-avatar" onClick={() => setSetupOpen(true)} aria-label={`${user.username}: my preparation`}>{initials}</button>
          </div>
        </header>
        <main className="pn-main">
          {view === "home" ? (
            <HomeView username={user.username} profile={profile} stage={stage} exams={exams} onSetup={() => setSetupOpen(true)} go={go}
              onTopic={(q) => { setSyllabusQ(q); go("syllabus"); }} onOpenTopic={openTopic} />
          ) : null}
          {view === "exam" ? <ExamView stage={stage} onStage={setStage} /> : null}
          {view === "syllabus" ? <SyllabusView key={`${stage}|${syllabusQ}`} stage={stage} initialQuery={syllabusQ} onOpen={openTopic} /> : null}
          {view === "topic" && topicId ? <TopicView key={`${stage}|${topicId}`} stage={stage} topicId={topicId} onBack={() => go("syllabus")} /> : null}
          {view === "library" ? <LibraryView isAdmin={user.role === "admin"} /> : null}
          {view === "shifa" ? (
            <div className="page page-wide">
              <header className="page-head"><div>
                <h1 className="page-title"><MessageCircleHeart size={22} />Dr. Shifa</h1>
                <p className="page-sub">Your tutor, from the Paediatrics textbooks and guidelines. Explain, tutor, or clinical reasoning.</p>
              </div></header>
              <ShifaChat stage={stage} />
            </div>
          ) : null}
        </main>
      </div>

      <nav className="pn-tabbar" aria-label="Main">
        {NAV.map((n) => (
          <button key={n.view} type="button" className={view === n.view ? "active" : ""} aria-current={view === n.view ? "page" : undefined}
            onClick={() => go(n.view)}>
            {n.icon}{n.short}
          </button>
        ))}
      </nav>

      {setupOpen && profile ? (
        <Setup profile={profile} onClose={() => setSetupOpen(false)} onSignOut={signOut}
          onSaved={(p) => { setProfile(p); if (p.stage) setStage(p.stage); setSetupOpen(false); }} />
      ) : null}
    </div>
  );
}
