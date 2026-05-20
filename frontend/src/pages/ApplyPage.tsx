import { Play, Send, ShieldAlert } from "lucide-react";
import type { ReactElement } from "react";
import { useState } from "react";

import { RunPanel } from "../components/RunPanel";
import { StatusPill } from "../components/StatusPill";
import { useApplyJobMutation, useApplyQueueMutation, useListJobsQuery } from "../services/api";
import type { ApplyRequest } from "../types";

export function ApplyPage(): ReactElement {
  const [runId, setRunId] = useState<number | null>(null);
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
      fit_label: form.fit_label || undefined
    },
    { pollingInterval: 5_000 }
  );
  const [applyJob, applyJobState] = useApplyJobMutation();
  const [applyQueue, applyQueueState] = useApplyQueueMutation();

  async function startJob(jobId: number): Promise<void> {
    const run = await applyJob({ jobId, body: form }).unwrap();
    setRunId(run.id);
  }

  async function startQueue(): Promise<void> {
    const run = await applyQueue(form).unwrap();
    setRunId(run.id);
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
          disabled={applyQueueState.isLoading}
          title="Apply queue"
        >
          <Send size={16} aria-hidden="true" />
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
          <span className="count-badge">{queueQuery.data?.jobs.length ?? 0}</span>
        </header>
        <div className="queue-list">
          {queueQuery.data?.jobs.map((job) => (
            <article key={job.id} className="queue-item">
              <div>
                <strong>{job.title}</strong>
                <span>{job.fit_score ?? "-"} / 100 | {job.recommended_resume_name || "-"}</span>
              </div>
              <StatusPill value={job.status} />
              <button
                className="icon-button"
                type="button"
                onClick={() => void startJob(job.id)}
                disabled={applyJobState.isLoading}
                title="Apply job"
              >
                <Play size={17} aria-hidden="true" />
              </button>
            </article>
          ))}
          {!queueQuery.data?.jobs.length ? <p className="empty-state">No actionable jobs.</p> : null}
        </div>
      </section>

      <RunPanel runId={runId} title="Apply run" />
    </div>
  );
}
