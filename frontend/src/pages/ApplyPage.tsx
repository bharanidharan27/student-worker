import { skipToken } from "@reduxjs/toolkit/query";
import { CheckCircle2, Eye, EyeOff, Loader2, Play, Send, ShieldAlert } from "lucide-react";
import type { ReactElement } from "react";
import { useEffect, useMemo, useRef, useState } from "react";

import { useAuthPrompt } from "../components/AuthPromptContext";
import { RunPanel } from "../components/RunPanel";
import { StatusPill } from "../components/StatusPill";
import {
  useApplyJobMutation,
  useApplyQueueMutation,
  useGetRunQuery,
  useListJobsQuery,
  useListRunsQuery,
  useUpdateJobStatusMutation
} from "../services/api";
import type { ApplyRequest, AutomationRun, Job, JobSort } from "../types";
import { isActiveRunStatus } from "../utils/runStatus";

const sortOptions: Array<{ label: string; value: JobSort }> = [
  { label: "Best fit", value: "best_fit" },
  { label: "Extracted order", value: "extracted" },
  { label: "Posted newest", value: "posted_desc" },
  { label: "Posted oldest", value: "posted_asc" }
];
const APPLY_RUN_STORAGE_KEY = "student-work-applier:showApplyRun";

export function ApplyPage(): ReactElement {
  const [runId, setRunId] = useState<number | null>(null);
  const [queueSort, setQueueSort] = useState<JobSort>("best_fit");
  const [showRunPanel, setShowRunPanel] = useState<boolean>(() => loadRunPanelPreference());
  const [form, setForm] = useState<ApplyRequest>({
    submit: false,
    confirm_submit: false,
    headed: true,
    limit: 3,
    min_score: 70,
    fit_label: "",
    click_timeout_ms: 10_000,
    applicant_name: "Bharanidharan Maheswaran"
  });
  const queueQuery = useListJobsQuery(
    {
      queue: true,
      limit: 25,
      min_score: form.min_score,
      fit_label: form.fit_label || undefined,
      sort: queueSort
    },
    { pollingInterval: 5_000 }
  );
  const refetchQueue = queueQuery.refetch;
  const [applyJob, applyJobState] = useApplyJobMutation();
  const [applyQueue, applyQueueState] = useApplyQueueMutation();
  const [updateJobStatus, updateJobStatusState] = useUpdateJobStatusMutation();
  const { requireSignIn } = useAuthPrompt();
  const [pendingJobId, setPendingJobId] = useState<number | null>(null);
  const runsQuery = useListRunsQuery(25, { pollingInterval: 2_000 });
  const selectedRunQuery = useGetRunQuery(runId ?? skipToken, {
    pollingInterval: runId ? 2_000 : 0
  });
  const refetchedRunId = useRef<number | null>(null);
  const activeApplyRuns = useMemo(
    () =>
      (runsQuery.data?.runs ?? []).filter(
        (run) => (run.kind === "apply_job" || run.kind === "apply_queue") && isActiveRunStatus(run.status)
      ),
    [runsQuery.data?.runs]
  );
  const activeJobRuns = useMemo(() => {
    const runsByJobId = new Map<number, AutomationRun>();
    for (const run of activeApplyRuns) {
      if (run.kind !== "apply_job") {
        continue;
      }
      const jobId = getRunJobId(run);
      if (jobId !== null) {
        runsByJobId.set(jobId, run);
      }
    }
    return runsByJobId;
  }, [activeApplyRuns]);
  const applyBusy = activeApplyRuns.length > 0 || applyJobState.isLoading || applyQueueState.isLoading;

  useEffect(() => {
    if (
      !selectedRunQuery.data ||
      isActiveRunStatus(selectedRunQuery.data.status) ||
      refetchedRunId.current === selectedRunQuery.data.id
    ) {
      return;
    }
    refetchedRunId.current = selectedRunQuery.data.id;
    setPendingJobId(null);
    void refetchQueue();
  }, [refetchQueue, selectedRunQuery.data]);

  async function startJob(jobId: number): Promise<void> {
    if (!requireSignIn()) {
      return;
    }
    setPendingJobId(jobId);
    try {
      const run = await applyJob({ jobId, body: form }).unwrap();
      setRunId(run.id);
    } catch {
      setPendingJobId(null);
    }
  }

  async function startQueue(): Promise<void> {
    if (!requireSignIn()) {
      return;
    }
    const run = await applyQueue(form).unwrap();
    setRunId(run.id);
  }

  async function markApplied(jobId: number): Promise<void> {
    await updateJobStatus({ jobId, status: "applied", note: "Marked applied from Apply queue." }).unwrap();
    await refetchQueue();
  }

  function toggleRunPanel(): void {
    setShowRunPanel((current) => {
      const next = !current;
      rememberRunPanelPreference(next);
      return next;
    });
  }

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <span className="eyebrow">Apply</span>
          <h1>Application Queue</h1>
        </div>
        <button
          className="button button-primary"
          type="button"
          onClick={() => void startQueue()}
          disabled={applyBusy}
          title="Apply queue"
        >
          {applyQueueState.isLoading || activeApplyRuns.some((run) => run.kind === "apply_queue") ? (
            <Loader2 className="spin" size={16} aria-hidden="true" />
          ) : (
            <Send size={16} aria-hidden="true" />
          )}
          Queue
        </button>
      </header>

      <section className="panel">
        <header className="panel-header">
          <h2>Controls</h2>
          <ShieldAlert size={18} aria-hidden="true" />
        </header>
        <div className="form-grid">
          <label>
            <span>Limit</span>
            <input
              type="number"
              min={1}
              max={25}
              value={form.limit}
              onChange={(event) => setForm({ ...form, limit: Number(event.target.value) })}
            />
          </label>
          <label>
            <span>Min score</span>
            <input
              type="number"
              min={0}
              max={100}
              value={form.min_score}
              onChange={(event) => setForm({ ...form, min_score: Number(event.target.value) })}
            />
          </label>
          <label>
            <span>Fit label</span>
            <select value={form.fit_label} onChange={(event) => setForm({ ...form, fit_label: event.target.value })}>
              <option value="">Any</option>
              <option value="Strong Fit">Strong Fit</option>
              <option value="Possible Fit">Possible Fit</option>
            </select>
          </label>
          <label>
            <span>Applicant</span>
            <input
              value={form.applicant_name}
              onChange={(event) => setForm({ ...form, applicant_name: event.target.value })}
            />
          </label>
        </div>
        <div className="toggle-row">
          <label className="check-row">
            <input
              type="checkbox"
              checked={form.headed}
              onChange={(event) => setForm({ ...form, headed: event.target.checked })}
            />
            <span>Headed browser</span>
          </label>
          <label className="check-row">
            <input
              type="checkbox"
              checked={form.submit}
              onChange={(event) =>
                setForm({
                  ...form,
                  submit: event.target.checked,
                  confirm_submit: event.target.checked ? form.confirm_submit : false
                })
              }
            />
            <span>Final submit</span>
          </label>
          <label className="check-row">
            <input
              type="checkbox"
              checked={form.confirm_submit}
              disabled={!form.submit}
              onChange={(event) => setForm({ ...form, confirm_submit: event.target.checked })}
            />
            <span>Confirm submit</span>
          </label>
        </div>
      </section>

      <section className="panel">
        <header className="panel-header">
          <h2>Ranked jobs</h2>
          <div className="header-actions">
            <label className="compact-field">
              <span>Sort</span>
              <select value={queueSort} onChange={(event) => setQueueSort(event.target.value as JobSort)}>
                {sortOptions.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
            <span className="count-badge">{queueQuery.data?.jobs.length ?? 0}</span>
            <button
              className="button"
              type="button"
              onClick={toggleRunPanel}
              aria-pressed={!showRunPanel}
              title={showRunPanel ? "Hide apply run" : "Show apply run"}
            >
              {showRunPanel ? <EyeOff size={16} aria-hidden="true" /> : <Eye size={16} aria-hidden="true" />}
              {showRunPanel ? "Hide Run" : "Show Run"}
            </button>
          </div>
        </header>
        <div className="queue-list">
          {queueQuery.data?.jobs.map((job) => (
            <QueueJobRow
              key={job.id}
              job={job}
              activeRun={activeJobRuns.get(job.id)}
              applyBusy={applyBusy}
              isPending={pendingJobId === job.id}
              isUpdatingStatus={updateJobStatusState.isLoading}
              onMarkApplied={markApplied}
              onStartJob={startJob}
            />
          ))}
          {!queueQuery.data?.jobs.length ? <p className="empty-state">No actionable jobs.</p> : null}
        </div>
      </section>

      {showRunPanel ? <RunPanel runId={runId} title="Apply run" /> : null}
    </div>
  );
}

interface QueueJobRowProps {
  activeRun: AutomationRun | undefined;
  applyBusy: boolean;
  isPending: boolean;
  isUpdatingStatus: boolean;
  job: Job;
  onMarkApplied: (jobId: number) => Promise<void>;
  onStartJob: (jobId: number) => Promise<void>;
}

function QueueJobRow({
  activeRun,
  applyBusy,
  isPending,
  isUpdatingStatus,
  job,
  onMarkApplied,
  onStartJob
}: QueueJobRowProps): ReactElement {
  const isJobActive = isPending || activeRun !== undefined;
  const isTerminalStatus = job.status === "applied" || job.status === "skipped";
  const applyTitle = isJobActive ? "Queued" : "Apply job";

  return (
    <article className="queue-item">
      <div>
        <strong>{job.title}</strong>
        <span>
          {job.fit_score ?? "-"} / 100 | {job.recommended_resume_name || "-"}
        </span>
        <span>
          Eligibility: {formatEligibility(job.eligibility_status)}
          {job.eligibility_override ? " | override" : ""}
        </span>
      </div>
      <StatusPill value={job.status} />
      <div className="queue-actions">
        <button
          className="icon-button"
          type="button"
          onClick={() => void onMarkApplied(job.id)}
          disabled={isUpdatingStatus || isJobActive || job.status === "applied"}
          title="Mark applied"
          aria-label={`Mark ${job.title} applied`}
        >
          <CheckCircle2 size={17} aria-hidden="true" />
        </button>
        <button
          className="icon-button"
          type="button"
          onClick={() => void onStartJob(job.id)}
          disabled={applyBusy || isTerminalStatus}
          title={applyTitle}
          aria-label={`${applyTitle}: ${job.title}`}
        >
          {isJobActive ? (
            <Loader2 className="spin" size={17} aria-hidden="true" />
          ) : (
            <Play size={17} aria-hidden="true" />
          )}
        </button>
      </div>
    </article>
  );
}

function formatEligibility(value: string | null): string {
  if (!value) {
    return "not reviewed";
  }
  return value.replaceAll("_", " ");
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

function loadRunPanelPreference(): boolean {
  if (typeof window === "undefined") {
    return true;
  }
  return window.localStorage.getItem(APPLY_RUN_STORAGE_KEY) !== "false";
}

function rememberRunPanelPreference(showRunPanel: boolean): void {
  if (typeof window !== "undefined") {
    window.localStorage.setItem(APPLY_RUN_STORAGE_KEY, String(showRunPanel));
  }
}
