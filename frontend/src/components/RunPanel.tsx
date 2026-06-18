import { skipToken } from "@reduxjs/toolkit/query";
import { CheckCircle2, Loader2, Play, RotateCw, StopCircle } from "lucide-react";
import type { ReactElement } from "react";

import { useContinueRunMutation, useGetRunEventsQuery, useGetRunQuery, useStopRunMutation } from "../services/api";
import { isActiveRunStatus } from "../utils/runStatus";
import { StatusPill } from "./StatusPill";

interface RunPanelProps {
  runId: number | null;
  title?: string;
  compact?: boolean;
}

export function RunPanel({ runId, title = "Current run", compact = false }: RunPanelProps): ReactElement {
  const runQuery = useGetRunQuery(runId ?? skipToken, {
    pollingInterval: runId ? 2_000 : 0
  });
  const run = runQuery.data;
  const active = isActiveRunStatus(run?.status);
  const eventsQuery = useGetRunEventsQuery(runId ?? skipToken, {
    pollingInterval: active ? 2_000 : 0
  });
  const logsRefreshing = runQuery.isFetching || eventsQuery.isFetching;
  const [continueRun, continueState] = useContinueRunMutation();
  const [stopRun, stopState] = useStopRunMutation();

  function refreshLogs(): void {
    if (!runId) {
      return;
    }
    void runQuery.refetch();
    void eventsQuery.refetch();
  }

  if (!runId) {
    return (
      <section className={`panel${compact ? " panel--compact" : ""}`}>
        <header className="panel-header">
          <h2>{title}</h2>
        </header>
        <p className="empty-state">No run selected.</p>
      </section>
    );
  }

  return (
    <section className={`panel${compact ? " panel--compact" : ""}`}>
      <header className="panel-header">
        <h2>{title}</h2>
        <div className="header-actions">
          {run && <StatusPill value={run.status} />}
          {active && <Loader2 className="spin" size={18} aria-label="Running" />}
          {run && active ? (
            <button
              className="button button-danger"
              type="button"
              onClick={() => void stopRun(run.id)}
              disabled={stopState.isLoading}
              title="Stop this run after the current safe checkpoint"
            >
              {stopState.isLoading ? (
                <Loader2 className="spin" size={16} aria-hidden="true" />
              ) : (
                <StopCircle size={16} aria-hidden="true" />
              )}
              Stop Run
            </button>
          ) : null}
        </div>
      </header>

      {runQuery.isFetching && !run ? <p className="empty-state">Loading run.</p> : null}
      {run ? (
        <div className="run-grid">
          <div>
            <span className="label">Run</span>
            <strong>#{run.id}</strong>
          </div>
          <div>
            <span className="label">Kind</span>
            <strong>{run.kind.replaceAll("_", " ")}</strong>
          </div>
          <div>
            <span className="label">Step</span>
            <strong>{run.current_step || "-"}</strong>
          </div>
          <div>
            <span className="label">Started</span>
            <strong>{run.started_at || run.created_at || "-"}</strong>
          </div>
        </div>
      ) : null}

      {run?.status === "waiting_for_user" ? (
        <button
          className="button button-primary"
          type="button"
          onClick={() => void continueRun(run.id)}
          disabled={continueState.isLoading}
          title="Continue"
        >
          <Play size={16} aria-hidden="true" />
          Done in browser
        </button>
      ) : null}

      {run?.error ? <p className="notice notice-error">{run.error}</p> : null}

      <div className="log-header">
        <h3>Logs</h3>
        <button
          className="icon-button icon-button-small"
          type="button"
          onClick={refreshLogs}
          disabled={logsRefreshing}
          aria-label="Refresh logs"
          title="Refresh logs"
        >
          <RotateCw className={logsRefreshing ? "spin" : undefined} size={15} aria-hidden="true" />
        </button>
      </div>
      <div className="log-box" role="log" aria-live="polite">
        {eventsQuery.data?.events.length ? (
          eventsQuery.data.events.map((event) => (
            <div key={event.id} className={`log-line log-line--${event.level}`}>
              <time>{event.created_at || ""}</time>
              <span>{event.message}</span>
            </div>
          ))
        ) : (
          <p className="empty-state">No log events yet.</p>
        )}
      </div>

      {run?.status === "completed" ? (
        <p className="notice notice-success">
          <CheckCircle2 size={16} aria-hidden="true" />
          Completed
        </p>
      ) : null}
    </section>
  );
}
