import {
  BriefcaseBusiness,
  ChevronDown,
  ClipboardList,
  History,
  Loader2,
  LogIn,
  PanelLeftClose,
  PanelLeftOpen,
  Radar,
  ShieldCheck,
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
  { to: "/scraper", label: "Scraper", icon: Radar },
  { to: "/jobs", label: "Jobs", icon: BriefcaseBusiness },
  { to: "/apply", label: "Apply", icon: ClipboardList },
  { to: "/runs", label: "Runs", icon: History }
];
const SIDEBAR_STORAGE_KEY = "student-work-applier:sidebarCollapsed";

type SessionAlertTone = "success" | "warn" | "error";

interface SessionAlert {
  message: string;
  tone: SessionAlertTone;
}

export function Shell(): ReactElement {
  const [sidebarCollapsed, setSidebarCollapsed] = useState<boolean>(() => loadSidebarPreference());
  const [promptOpen, setPromptOpen] = useState<boolean>(false);
  const [promptDismissed, setPromptDismissed] = useState<boolean>(false);
  const [lastSignedIn, setLastSignedIn] = useState<boolean | null>(null);
  const [sessionAutoCheckKey, setSessionAutoCheckKey] = useState<string | null>(null);
  const [invalidSessionKey, setInvalidSessionKey] = useState<string | null>(null);
  const [accountMenuOpen, setAccountMenuOpen] = useState<boolean>(false);
  const [manualSessionCheckActive, setManualSessionCheckActive] = useState<boolean>(false);
  const [sessionAlert, setSessionAlert] = useState<SessionAlert | null>(null);
  const statusQuery = useGetSessionStatusQuery(undefined, { pollingInterval: 5_000 });
  const [checkSession, checkSessionState] = useCheckSessionMutation();
  const currentSession = checkSessionState.data?.authenticated ? checkSessionState.data : statusQuery.data;
  const sessionCheckKey = statusQuery.data?.exists
    ? `${statusQuery.data.auth_state_path}:${statusQuery.data.modified_at ?? ""}:${statusQuery.data.size_bytes}`
    : statusQuery.data
      ? "missing"
      : null;
  const sessionMarkedInvalid = invalidSessionKey !== null && (invalidSessionKey === sessionCheckKey || sessionCheckKey === null);
  const signedIn = Boolean(currentSession?.authenticated) && !sessionMarkedInvalid;
  const checkingSession = Boolean(
    statusQuery.isLoading ||
      checkSessionState.isLoading ||
      manualSessionCheckActive ||
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
      setPromptOpen(false);
    } else if (signedIn) {
      setPromptOpen(false);
    } else if (!signedIn && !promptDismissed) {
      setPromptOpen(true);
    } else if (!signedIn) {
      setPromptOpen(false);
    }
  }, [checkingSession, signedIn, promptDismissed]);

  useEffect(() => {
    if (!signedIn) {
      setAccountMenuOpen(false);
    }
  }, [signedIn]);

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

  const dismissSessionAlert = useCallback((): void => {
    setSessionAlert(null);
  }, []);

  async function handleCheckSignIn(): Promise<void> {
    setAccountMenuOpen(false);
    setSessionAlert(null);
    setManualSessionCheckActive(true);
    try {
      const result = await checkSession().unwrap();
      const checkedName = result.display_name?.trim() || result.email?.trim() || signedInLabel || "Workday";
      if (result.valid && result.authenticated) {
        setInvalidSessionKey(null);
        setSessionAlert({
          tone: "success",
          message: `Sign-in is valid for ${checkedName}. You can scrape and apply.`
        });
      } else {
        setInvalidSessionKey(sessionCheckKey ?? "manual-invalid");
        setSessionAlert({
          tone: "warn",
          message: "Sign-in needs attention. Sign in again before scraping or applying."
        });
        setPromptDismissed(false);
        setPromptOpen(true);
      }
    } catch {
      setSessionAlert({
        tone: "error",
        message: "Could not check sign-in. Try again in a moment."
      });
    } finally {
      setManualSessionCheckActive(false);
    }
  }

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
      {sessionAlert ? (
        <div className={`session-alert session-alert--${sessionAlert.tone}`} role="alert">
          <span>{sessionAlert.message}</span>
          <button className="icon-button" type="button" onClick={dismissSessionAlert} aria-label="Dismiss session alert">
            <X size={14} aria-hidden="true" />
          </button>
        </div>
      ) : null}
      {checkingSession ? (
        <div className="session-check-overlay" role="status" aria-live="polite" aria-label="Checking sign-in status">
          <div className="session-check-card">
            <Loader2 className="spin" size={28} aria-hidden="true" />
            <strong>Checking sign-in status</strong>
            <span>This will only take a moment.</span>
          </div>
        </div>
      ) : null}
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
                <span className="signed-in-pill-label">Checking</span>
              </span>
            ) : signedIn ? (
              <div className="account-menu-wrap">
                <button
                  className="signed-in-pill signed-in-pill--button"
                  type="button"
                  onClick={() => setAccountMenuOpen((current) => !current)}
                  aria-haspopup="menu"
                  aria-expanded={accountMenuOpen}
                  aria-label={`Signed in as ${signedInLabel}`}
                  title={email || displayName || "Workday session active"}
                >
                  <UserCircle2 size={16} aria-hidden="true" />
                  <span className="signed-in-pill-label">Signed in</span>
                  <span className="signed-in-pill-name">{signedInLabel}</span>
                  <ChevronDown size={14} aria-hidden="true" />
                </button>
                {accountMenuOpen ? (
                  <div className="account-menu" role="menu" aria-label="Workday account">
                    <div className="account-menu-summary">
                      <span className="eyebrow">Workday</span>
                      <strong>{signedInLabel}</strong>
                    </div>
                    <button className="account-menu-item" type="button" role="menuitem" onClick={() => void handleCheckSignIn()}>
                      <ShieldCheck size={16} aria-hidden="true" />
                      Check sign-in status
                    </button>
                  </div>
                ) : null}
              </div>
            ) : null}
            {!signedIn ? (
              <button className="button button-primary" type="button" onClick={openPrompt} disabled={checkingSession} title="Sign in with Workday">
                <LogIn size={16} aria-hidden="true" />
                Sign in
              </button>
            ) : null}
          </div>
        </header>
        <AuthPromptContext.Provider value={authPromptContext}>
          <main className="content">
            <Outlet />
          </main>
        </AuthPromptContext.Provider>
      </div>
      <LoginPrompt open={promptOpen && !checkingSession && !signedIn} onDismiss={closePrompt} />
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
