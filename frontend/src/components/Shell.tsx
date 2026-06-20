import {
  BriefcaseBusiness,
  ClipboardList,
  History,
  KeyRound,
  Loader2,
  LogIn,
  PanelLeftClose,
  PanelLeftOpen,
  Radar,
  UserCircle2,
  X
} from "lucide-react";
import type { ReactElement } from "react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";

import { AuthPromptContext } from "./AuthPromptContext";
import { LoginPrompt } from "./LoginPrompt";
import { useCheckSessionMutation, useGetSessionStatusQuery } from "../services/api";

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
  const [sessionAutoCheckKey, setSessionAutoCheckKey] = useState<string | null>(null);
  const statusQuery = useGetSessionStatusQuery(undefined, { pollingInterval: 5_000 });
  const [checkSession, checkSessionState] = useCheckSessionMutation();
  const currentSession = checkSessionState.data?.authenticated ? checkSessionState.data : statusQuery.data;
  const signedIn = Boolean(currentSession?.authenticated);
  const sessionCheckKey = statusQuery.data?.exists
    ? `${statusQuery.data.auth_state_path}:${statusQuery.data.modified_at ?? ""}:${statusQuery.data.size_bytes}`
    : statusQuery.data
      ? "missing"
      : null;
  const checkingSession = Boolean(
    statusQuery.isLoading ||
      checkSessionState.isLoading ||
      (statusQuery.data?.exists && !signedIn && sessionCheckKey !== null && sessionAutoCheckKey !== sessionCheckKey)
  );

  useEffect(() => {
    if (!statusQuery.data || sessionCheckKey === null) {
      return;
    }
    if (!statusQuery.data.exists || statusQuery.data.authenticated) {
      setSessionAutoCheckKey(sessionCheckKey);
      return;
    }
    if (sessionAutoCheckKey === sessionCheckKey || checkSessionState.isLoading) {
      return;
    }
    void checkSession()
      .unwrap()
      .catch(() => undefined)
      .finally(() => setSessionAutoCheckKey(sessionCheckKey));
  }, [checkSession, checkSessionState.isLoading, sessionAutoCheckKey, sessionCheckKey, statusQuery.data]);

  useEffect(() => {
    if (checkingSession) {
      return;
    }
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
  }, [checkingSession, signedIn, lastSignedIn]);

  useEffect(() => {
    if (checkingSession) {
      setPromptOpen(true);
    } else if (!signedIn && !promptDismissed) {
      setPromptOpen(true);
    } else if (!signedIn) {
      setPromptOpen(false);
    }
  }, [checkingSession, signedIn, promptDismissed]);

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

  const displayName = currentSession?.display_name?.trim() || "";
  const email = currentSession?.email?.trim() || "";
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
            {checkingSession ? (
              <span className="signed-in-pill signed-in-pill--muted" title="Checking Workday session">
                <Loader2 className="spin" size={16} aria-hidden="true" />
                <span className="signed-in-pill-label">Checking session</span>
              </span>
            ) : signedIn ? (
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
              disabled={checkingSession}
              title={checkingSession ? "Checking Workday session" : signedIn ? "Refresh Workday session" : "Sign in with Workday"}
            >
              {checkingSession ? (
                <Loader2 className="spin" size={16} aria-hidden="true" />
              ) : signedIn ? (
                <KeyRound size={16} aria-hidden="true" />
              ) : (
                <LogIn size={16} aria-hidden="true" />
              )}
              {checkingSession ? "Loading" : signedIn ? "Refresh session" : "Sign in"}
            </button>
          </div>
        </header>
        <AuthPromptContext.Provider value={authPromptContext}>
          <main className="content">
            <Outlet />
          </main>
        </AuthPromptContext.Provider>
      </div>
      <LoginPrompt open={promptOpen} onDismiss={closePrompt} checkingSession={checkingSession} />
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
