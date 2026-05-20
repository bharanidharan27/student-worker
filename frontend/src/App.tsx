import type { ReactElement } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import { Shell } from "./components/Shell";
import { ApplyPage } from "./pages/ApplyPage";
import { JobsPage } from "./pages/JobsPage";
import { RunHistoryPage } from "./pages/RunHistoryPage";
import { ScraperPage } from "./pages/ScraperPage";
import { SessionPage } from "./pages/SessionPage";

export function App(): ReactElement {
  return (
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<SessionPage />} />
        <Route path="scraper" element={<ScraperPage />} />
        <Route path="jobs" element={<JobsPage />} />
        <Route path="apply" element={<ApplyPage />} />
        <Route path="runs" element={<RunHistoryPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
