import { BriefcaseBusiness, ClipboardList, History, KeyRound, LogIn, PanelLeftClose, PanelLeftOpen, Radar, UserCircle2, X } from "lucide-react";
import type { ReactElement } from "react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";

import { AuthPromptContext } from "./AuthPromptContext";
import { LoginPrompt } from "./LoginPrompt";
import { useGetSessionStatusQuery } from "../services/api";

const navigation = [
  { to: "/jobs", label: "Jobs", icon: BriefcaseBusiness },
  { to: "/scraper", label: "Scraper", icon: Radar },
  { to: "/apply", label: "Apply", icon: ClipboardList },
  { to: "/runs", label: "Runs", icon: History }
];
const SIDEBAR_STORAGE_KEY = "student-work-applier:sidebarCollapsed";

export function Shell(): ReactElement {
  const [sidebarCollapsed, setSidebarCollapsed] = useState<boolean>(() => loadSidebarPreference());
  const [promptOpen, setPromptOpen] = useState<boolean>(false);
  const [promptDismissed, setPromptDismissed] = useState<boolean>(false);
  const [lastSignedIn, setLastSignedIn] = useState<boolean | null>(null);
  const statusQuery = useGetSessionStatusQuery(undefined, { pollingInterval: 5_000 });
  const signedIn = Boolean(statusQuery.data?.authenticated);

  useEffect(() => {
    if (lastSignedIn === null) {
      setLastSignedIn(signedIn);
      return;
    }
    if (signedIn) {
      setPromptDismissed(false);
      setPromptOpen(false);
    } else if (lastSignedIn) {
      setPromptDismissed(false);
    }
    setLastSignedIn(signedIn);
  }, [signedIn, lastSignedIn]);

  useEffect(() => {
    if (!signedIn && !promptDismissed) {
      setPromptOpen(true);
    } else {
      setPromptOpen(false);
    }
  }, [signedIn, promptDismissed]);

  function toggleSidebar(): void {
    setSidebarCollapsed((current) => {
      const next = !current;
      rememberSidebarPreference(next);
      return next;
    });
  }

  const openPrompt = useCallback((): void => {
    setPromptDismissed(false);
    setPromptOpen(true);
  }, []);

  const closePrompt = useCallback((): void => {
    setPromptDismissed(true);
    setPromptOpen(false);
  }, []);

  const authPromptContext = useMemo(
    () => ({
      openLoginPrompt: openPrompt,
      requireSignIn: () => {
        if (signedIn) {
          return true;
        }
        openPrompt();
        return false;
      },
      signedIn
    }),
    [openPrompt, signedIn]
  );

  const displayName = statusQuery.data?.display_name?.trim() || "";
  const email = statusQuery.data?.email?.trim() || "";
  const signedInLabel = displayName || email;

  return (
    <div className={`shell ${sidebarCollapsed ? "shell--sidebar-collapsed" : ""}`}>
      <aside className="sidebar" aria-label="Primary">
        <div className="brand-row">
          <div className="brand">
            <span className="brand-mark">SW</span>
            <div className="brand-copy">
              <strong>Student Work</strong>
              <span>Operational Console</span>
            </div>
          </div>
          <button
            className="icon-button sidebar-toggle"
            type="button"
            onClick={toggleSidebar}
            aria-label={sidebarCollapsed ? "Show navigation panel" : "Hide navigation panel"}
            aria-pressed={sidebarCollapsed}
            title={sidebarCollapsed ? "Show navigation" : "Hide navigation"}
          >
            {sidebarCollapsed ? <PanelLeftOpen size={18} aria-hidden="true" /> : <PanelLeftClose size={18} aria-hidden="true" />}
          </button>
        </div>
        <nav className="nav-list">
          {navigation.map((item) => {
            const Icon = item.icon;
            return (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) => `nav-item${isActive ? " nav-item--active" : ""}`}
                title={sidebarCollapsed ? item.label : undefined}
              >
                <Icon size={18} aria-hidden="true" />
                <span>{item.label}</span>
              </NavLink>
            );
          })}
        </nav>
      </aside>
      <div className="shell-main">
        <header className="topbar" aria-label="Account status">
          <div className="topbar-title">
            <span className="eyebrow">Console</span>
            <strong>Student Work Operational Console</strong>
          </div>
          <div className="topbar-actions">
            {signedIn ? (
              <span className="signed-in-pill" title={email || displayName || "Workday session active"}>
                <UserCircle2 size={16} aria-hidden="true" />
                <span className="signed-in-pill-label">Signed in</span>
                <span className="signed-in-pill-name">{signedInLabel}</span>
              </span>
            ) : (
              <span className="signed-in-pill signed-in-pill--muted" title="No Workday session saved">
                <UserCircle2 size={16} aria-hidden="true" />
                <span className="signed-in-pill-label">Not signed in</span>
              </span>
            )}
            {promptOpen ? (
              <button
                className="icon-button"
                type="button"
                onClick={closePrompt}
                aria-label="Hide sign-in prompt"
                title="Hide sign-in prompt"
              >
                <X size={16} aria-hidden="true" />
              </button>
            ) : null}
            <button
              className="button button-primary"
              type="button"
              onClick={openPrompt}
              title={signedIn ? "Refresh Workday session" : "Sign in with Workday"}
            >
              {signedIn ? <KeyRound size={16} aria-hidden="true" /> : <LogIn size={16} aria-hidden="true" />}
              {signedIn ? "Refresh session" : "Sign in"}
            </button>
          </div>
        </header>
        <AuthPromptContext.Provider value={authPromptContext}>
          <main className="content">
            <Outlet />
          </main>
        </AuthPromptContext.Provider>
      </div>
      <LoginPrompt open={promptOpen} onDismiss={closePrompt} />
    </div>
  );
}

function loadSidebarPreference(): boolean {
  if (typeof window === "undefined") {
    return false;
  }
  return window.localStorage.getItem(SIDEBAR_STORAGE_KEY) === "true";
}

function rememberSidebarPreference(collapsed: boolean): void {
  if (typeof window !== "undefined") {
    window.localStorage.setItem(SIDEBAR_STORAGE_KEY, String(collapsed));
  }
}
