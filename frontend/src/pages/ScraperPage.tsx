import { skipToken } from "@reduxjs/toolkit/query";
import { Loader2, Play, Radar } from "lucide-react";
import type { ReactElement } from "react";
import { useEffect, useMemo, useState } from "react";

import { RunPanel } from "../components/RunPanel";
import { useGetJobQuery, useListRunsQuery, useStartScrapeMutation } from "../services/api";
import type { AutomationRun, ScrapeRequest } from "../types";
import { isActiveRunStatus } from "../utils/runStatus";

const SCRAPER_RUN_STORAGE_KEY = "student-work-applier:lastScrapeRunId";

export function ScraperPage(): ReactElement {
  const [runId, setRunId] = useState<number | null>(() => loadStoredScrapeRunId());
  const [form, setForm] = useState<ScrapeRequest>({
    limit: 10,
    headed: false,
    wait_ms: 750,
    max_scrolls: 50,
    idle_rounds: 3,
    click_timeout_ms: 5_000,
    debug_dump_dir: "outputs/debug"
  });
  const [startScrape, startState] = useStartScrapeMutation();
  const runsQuery = useListRunsQuery(50, { pollingInterval: 2_000 });
  const runs = runsQuery.data?.runs ?? [];
  const activeRuns = useMemo(() => runs.filter((run) => isActiveRunStatus(run.status)), [runs]);
  const latestScrapeRun = useMemo(() => runs.find((run) => run.kind === "scrape") ?? null, [runs]);
  const activeScrapeRun = useMemo(
    () => activeRuns.find((run) => run.kind === "scrape") ?? null,
    [activeRuns]
  );
  const activeBrowserRun = activeRuns[0] ?? null;
  const activeBrowserJobId = activeBrowserRun && activeBrowserRun.kind !== "scrape" ? getRunJobId(activeBrowserRun) : null;
  const activeBrowserJobQuery = useGetJobQuery(activeBrowserJobId ?? skipToken);
  const startBlocked = startState.isLoading || activeBrowserRun !== null;

  useEffect(() => {
    const nextRun = activeScrapeRun ?? latestScrapeRun;
    if (!nextRun || runId === nextRun.id) {
      return;
    }
    rememberScrapeRun(nextRun.id);
    setRunId(nextRun.id);
  }, [activeScrapeRun, latestScrapeRun, runId]);

  async function handleStart(): Promise<void> {
    const run = await startScrape(form).unwrap();
    rememberScrapeRun(run.id);
    setRunId(run.id);
  }

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <span className="eyebrow">Scraper</span>
          <h1>Workday Extraction</h1>
        </div>
        <button
          className="button button-primary"
          type="button"
          onClick={() => void handleStart()}
          disabled={startBlocked}
          title="Start scrape"
        >
          {startState.isLoading || activeScrapeRun ? (
            <Loader2 className="spin" size={16} aria-hidden="true" />
          ) : (
            <Play size={16} aria-hidden="true" />
          )}
          Start
        </button>
      </header>

      {activeBrowserRun && activeBrowserRun.kind !== "scrape" ? (
        <p className="notice notice-warn">
          Browser worker is busy with {formatBusyRun(activeBrowserRun, activeBrowserJobQuery.data?.title)}.
        </p>
      ) : null}

      <section className="panel">
        <header className="panel-header">
          <h2>Settings</h2>
          <Radar size={18} aria-hidden="true" />
        </header>
        <div className="form-grid">
          <label>
            <span>Limit</span>
            <input
              type="number"
              min={1}
              max={500}
              value={form.limit ?? ""}
              onChange={(event) => setForm({ ...form, limit: Number(event.target.value) || null })}
            />
          </label>
          <label>
            <span>Max scrolls</span>
            <input
              type="number"
              min={1}
              max={250}
              value={form.max_scrolls}
              onChange={(event) => setForm({ ...form, max_scrolls: Number(event.target.value) })}
            />
          </label>
          <label>
            <span>Idle rounds</span>
            <input
              type="number"
              min={1}
              max={25}
              value={form.idle_rounds}
              onChange={(event) => setForm({ ...form, idle_rounds: Number(event.target.value) })}
            />
          </label>
          <label>
            <span>Wait ms</span>
            <input
              type="number"
              min={0}
              max={10_000}
              value={form.wait_ms}
              onChange={(event) => setForm({ ...form, wait_ms: Number(event.target.value) })}
            />
          </label>
          <label>
            <span>Click timeout ms</span>
            <input
              type="number"
              min={500}
              max={60_000}
              value={form.click_timeout_ms}
              onChange={(event) => setForm({ ...form, click_timeout_ms: Number(event.target.value) })}
            />
          </label>
          <label>
            <span>Debug folder</span>
            <input
              value={form.debug_dump_dir || ""}
              onChange={(event) => setForm({ ...form, debug_dump_dir: event.target.value })}
            />
          </label>
        </div>
        <label className="check-row">
          <input
            type="checkbox"
            checked={form.headed}
            onChange={(event) => setForm({ ...form, headed: event.target.checked })}
          />
          <span>Headed browser</span>
        </label>
      </section>

      <RunPanel runId={runId} title="Scrape run" />
    </div>
  );
}

function loadStoredScrapeRunId(): number | null {
  if (typeof window === "undefined") {
    return null;
  }
  const value = window.localStorage.getItem(SCRAPER_RUN_STORAGE_KEY);
  if (!value) {
    return null;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function rememberScrapeRun(runId: number): void {
  if (typeof window !== "undefined") {
    window.localStorage.setItem(SCRAPER_RUN_STORAGE_KEY, String(runId));
  }
}

function formatRunKind(run: AutomationRun): string {
  return run.kind.replaceAll("_", " ");
}

function formatBusyRun(run: AutomationRun, jobTitle?: string | null): string {
  const runKind = formatRunKind(run);
  if (getRunJobId(run) === null) {
    return `${runKind} #${run.id}`;
  }
  const normalizedTitle = jobTitle?.trim();
  return normalizedTitle ? `${runKind} for ${normalizedTitle}` : `${runKind} for this job`;
}

function getRunJobId(run: AutomationRun): number | null {
  const jobId = run.params.job_id;
  if (typeof jobId === "number") {
    return jobId;
  }
  if (typeof jobId === "string" && jobId.trim()) {
    const parsed = Number(jobId);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}
