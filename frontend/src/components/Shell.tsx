import { BriefcaseBusiness, ClipboardList, History, KeyRound, Radar } from "lucide-react";
import type { ReactElement } from "react";
import { NavLink, Outlet } from "react-router-dom";

const navigation = [
  { to: "/", label: "Session", icon: KeyRound },
  { to: "/scraper", label: "Scraper", icon: Radar },
  { to: "/jobs", label: "Jobs", icon: BriefcaseBusiness },
  { to: "/apply", label: "Apply", icon: ClipboardList },
  { to: "/runs", label: "Runs", icon: History }
];

export function Shell(): ReactElement {
  return (
    <div className="shell">
      <aside className="sidebar" aria-label="Primary">
        <div className="brand">
          <span className="brand-mark">SW</span>
          <div>
            <strong>Student Work</strong>
            <span>Operational Console</span>
          </div>
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
