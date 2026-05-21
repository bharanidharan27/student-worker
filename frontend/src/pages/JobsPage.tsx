import { Check, ExternalLink, Eye, EyeOff, Search, Undo2, X } from "lucide-react";
import type { ReactElement } from "react";
import { useEffect, useState } from "react";

import { StatusPill } from "../components/StatusPill";
import { useGetJobQuery, useListJobsQuery, useUpdateJobStatusMutation } from "../services/api";
import type { JobFilters, JobSort } from "../types";

const statuses = ["", "new", "reviewing", "applied", "skipped"];
const labels = ["", "Strong Fit", "Possible Fit", "Skip"];
const sortOptions: Array<{ label: string; value: JobSort }> = [
  { label: "Best fit", value: "best_fit" },
  { label: "Extracted order", value: "extracted" },
  { label: "Posted newest", value: "posted_desc" },
  { label: "Posted oldest", value: "posted_asc" }
];
const JOB_DETAIL_STORAGE_KEY = "student-work-applier:showJobDetail";

export function JobsPage(): ReactElement {
  const [filters, setFilters] = useState<JobFilters>({ limit: 100, sort: "best_fit" });
  const [selectedJobId, setSelectedJobId] = useState<number | null>(null);
  const [showDetails, setShowDetails] = useState<boolean>(() => loadDetailPreference());
  const jobsQuery = useListJobsQuery(filters, { pollingInterval: 5_000 });
  const selectedQuery = useGetJobQuery(selectedJobId ?? 0, { skip: selectedJobId === null || !showDetails });
  const [updateStatus, updateState] = useUpdateJobStatusMutation();

  const selected = showDetails ? selectedQuery.data : undefined;
  const jobs = jobsQuery.data?.jobs ?? [];

  useEffect(() => {
    if (jobs.length === 0 && selectedJobId !== null) {
      setSelectedJobId(null);
      return;
    }
    if (selectedJobId === null && jobs.length > 0) {
      setSelectedJobId(jobs[0].id);
    }
    if (selectedJobId !== null && jobs.length > 0 && !jobs.some((job) => job.id === selectedJobId)) {
      setSelectedJobId(jobs[0].id);
    }
  }, [jobs, selectedJobId]);

  function patchFilters(next: Partial<JobFilters>): void {
    setFilters((current) => ({ ...current, ...next }));
  }

  function clearDateFilters(): void {
    patchFilters({ posted_from: undefined, posted_to: undefined });
  }

  function toggleDetails(): void {
    setShowDetails((current) => {
      const next = !current;
      rememberDetailPreference(next);
      return next;
    });
  }

  async function mark(status: "reviewing" | "applied" | "skipped" | "new", note?: string): Promise<void> {
    if (!selectedJobId) {
      return;
    }
    await updateStatus({ jobId: selectedJobId, status, note }).unwrap();
  }

  return (
    <div className={`page ${showDetails ? "page-split" : "page-full"}`}>
      <section className="list-pane">
        <header className="page-header">
          <div>
            <span className="eyebrow">Jobs</span>
            <h1>Saved Queue</h1>
          </div>
          <div className="header-actions">
            <span className="count-badge">{jobs.length}</span>
            <button
              className="button"
              type="button"
              onClick={toggleDetails}
              aria-pressed={!showDetails}
              title={showDetails ? "Hide selected job" : "Show selected job"}
            >
              {showDetails ? <EyeOff size={16} aria-hidden="true" /> : <Eye size={16} aria-hidden="true" />}
              {showDetails ? "Hide Details" : "Show Details"}
            </button>
          </div>
        </header>

        <div className="filter-row">
          <label className="search-field">
            <Search size={16} aria-hidden="true" />
            <input
              placeholder="Search"
              value={filters.q || ""}
              onChange={(event) => patchFilters({ q: event.target.value })}
            />
          </label>
          <select value={filters.status || ""} onChange={(event) => patchFilters({ status: event.target.value })}>
            {statuses.map((status) => (
              <option key={status || "all"} value={status}>
                {status || "All statuses"}
              </option>
            ))}
          </select>
          <select
            value={filters.fit_label || ""}
            onChange={(event) => patchFilters({ fit_label: event.target.value })}
          >
            {labels.map((label) => (
              <option key={label || "all"} value={label}>
                {label || "All fits"}
              </option>
            ))}
          </select>
          <input
            className="score-input"
            type="number"
            min={0}
            max={100}
            placeholder="Min score"
            value={filters.min_score ?? ""}
            onChange={(event) =>
              patchFilters({ min_score: event.target.value ? Number(event.target.value) : undefined })
            }
          />
        </div>

        <div className="date-filter-row">
          <label>
            <span>Posted from</span>
            <input
              type="date"
              value={filters.posted_from || ""}
              onChange={(event) => patchFilters({ posted_from: event.target.value || undefined })}
            />
          </label>
          <label>
            <span>Posted to</span>
            <input
              type="date"
              value={filters.posted_to || ""}
              onChange={(event) => patchFilters({ posted_to: event.target.value || undefined })}
            />
          </label>
          <label>
            <span>Sort</span>
            <select
              value={filters.sort || "best_fit"}
              onChange={(event) => patchFilters({ sort: event.target.value as JobSort })}
            >
              {sortOptions.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <button
            className="button"
            type="button"
            onClick={clearDateFilters}
            disabled={!filters.posted_from && !filters.posted_to}
            title="Clear date filters"
          >
            <X size={16} aria-hidden="true" />
            Dates
          </button>
        </div>

        <div className="table-wrap">
          <table className="jobs-table">
            <colgroup>
              <col className="jobs-col-title" />
              <col className="jobs-col-fit" />
              <col className="jobs-col-status" />
              <col className="jobs-col-applied" />
              <col className="jobs-col-resume" />
            </colgroup>
            <thead>
              <tr>
                <th>Title</th>
                <th>Fit</th>
                <th>Status</th>
                <th>Applied</th>
                <th>Resume</th>
              </tr>
            </thead>
            <tbody>
              {jobsQuery.data?.jobs.map((job) => (
                <tr
                  key={job.id}
                  className={selectedJobId === job.id ? "selected-row" : ""}
                  onClick={() => setSelectedJobId(job.id)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      setSelectedJobId(job.id);
                    }
                  }}
                  tabIndex={0}
                >
                  <td className="job-title-cell">
                    <strong>{job.title}</strong>
                    <span>
                      {job.location || job.workday_id || "-"}
                      {job.posting_date ? ` | Posted ${job.posting_date}` : ""}
                    </span>
                  </td>
                  <td className="fit-cell">
                    <strong>{job.fit_score ?? "-"}</strong>
                    <span>{job.fit_label || "-"}</span>
                  </td>
                  <td>
                    <StatusPill value={job.status} />
                  </td>
                  <td className="applied-cell">
                    <span>{formatAppliedAt(job.applied_at)}</span>
                  </td>
                  <td className="resume-cell" title={job.recommended_resume_name || ""}>
                    <span>{job.recommended_resume_name || "-"}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!jobs.length ? <p className="empty-state">No jobs match.</p> : null}
        </div>
      </section>

      {showDetails ? (
        <section className="detail-pane">
        {selectedQuery.isFetching && !selected ? (
          <p className="empty-state empty-state-panel">Loading job detail.</p>
        ) : selected ? (
          <>
            <header className="panel-header">
              <div>
                <span className="eyebrow">Selected Job</span>
                <h2>{selected.title}</h2>
              </div>
              <StatusPill value={selected.status} />
            </header>
            <div className="job-meta">
              <span>{selected.workday_id || "-"}</span>
              <span>{selected.location || "-"}</span>
              <span>{selected.posting_date || "-"}</span>
              <span>Applied {formatAppliedAt(selected.applied_at)}</span>
              <span>{selected.fit_score ?? "-"} / 100</span>
            </div>
            <div className="toolbar">
              <button type="button" className="button" onClick={() => void mark("reviewing")} disabled={updateState.isLoading}>
                <Search size={16} aria-hidden="true" />
                Review
              </button>
              <button type="button" className="button" onClick={() => void mark("applied")} disabled={updateState.isLoading}>
                <Check size={16} aria-hidden="true" />
                Applied
              </button>
              {selected.status === "applied" ? (
                <button
                  type="button"
                  className="button"
                  onClick={() => void mark("new", "Moved back to the Apply queue from Jobs.")}
                  disabled={updateState.isLoading}
                  title="Move back to Apply queue"
                >
                  <Undo2 size={16} aria-hidden="true" />
                  Unapply
                </button>
              ) : null}
              <button type="button" className="button" onClick={() => void mark("skipped")} disabled={updateState.isLoading}>
                <X size={16} aria-hidden="true" />
                Skip
              </button>
              {selected.url ? (
                <a className="button" href={selected.url} target="_blank" rel="noreferrer">
                  <ExternalLink size={16} aria-hidden="true" />
                  Open
                </a>
              ) : null}
              <button
                type="button"
                className="button"
                onClick={toggleDetails}
                aria-pressed={!showDetails}
                title="Hide selected job"
              >
                <EyeOff size={16} aria-hidden="true" />
                Hide Details
              </button>
            </div>
            <dl className="detail-list">
              <dt>Department</dt>
              <dd>{selected.department || "-"}</dd>
              <dt>Hours</dt>
              <dd>{selected.hours || "-"}</dd>
              <dt>Pay</dt>
              <dd>{selected.pay_rate || "-"}</dd>
              <dt>Applied</dt>
              <dd>{formatAppliedAt(selected.applied_at)}</dd>
              <dt>Resume</dt>
              <dd>{selected.recommended_resume_path || "-"}</dd>
              <dt>Notes</dt>
              <dd>{selected.application_notes || "-"}</dd>
            </dl>
            <h3>Description</h3>
            <pre className="description-box">{selected.raw_description || "No description stored."}</pre>
          </>
        ) : (
          <p className="empty-state empty-state-panel">Select a job.</p>
        )}
        </section>
      ) : null}
    </div>
  );
}

function loadDetailPreference(): boolean {
  if (typeof window === "undefined") {
    return true;
  }
  return window.localStorage.getItem(JOB_DETAIL_STORAGE_KEY) !== "false";
}

function rememberDetailPreference(showDetails: boolean): void {
  if (typeof window !== "undefined") {
    window.localStorage.setItem(JOB_DETAIL_STORAGE_KEY, String(showDetails));
  }
}

function formatAppliedAt(value: string | null): string {
  if (!value) {
    return "-";
  }
  const match = value.match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::\d{2})?/);
  if (!match) {
    return value;
  }
  const [, year, month, day, hour, minute] = match;
  return `${month}/${day}/${year} ${hour}:${minute}`;
}
