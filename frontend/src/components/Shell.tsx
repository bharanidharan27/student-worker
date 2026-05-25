import { BriefcaseBusiness, ClipboardList, History, KeyRound, PanelLeftClose, PanelLeftOpen, Radar } from "lucide-react";
import type { ReactElement } from "react";
import { useState } from "react";
import { NavLink, Outlet } from "react-router-dom";

const navigation = [
  { to: "/", label: "Session", icon: KeyRound },
  { to: "/scraper", label: "Scraper", icon: Radar },
  { to: "/jobs", label: "Jobs", icon: BriefcaseBusiness },
  { to: "/apply", label: "Apply", icon: ClipboardList },
  { to: "/runs", label: "Runs", icon: History }
];
const SIDEBAR_STORAGE_KEY = "student-work-applier:sidebarCollapsed";

export function Shell(): ReactElement {
  const [sidebarCollapsed, setSidebarCollapsed] = useState<boolean>(() => loadSidebarPreference());

  function toggleSidebar(): void {
    setSidebarCollapsed((current) => {
      const next = !current;
      rememberSidebarPreference(next);
      return next;
    });
  }

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
                end={item.to === "/"}
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
      <main className="content">
        <Outlet />
      </main>
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
