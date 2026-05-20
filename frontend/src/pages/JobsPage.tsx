import { Check, ExternalLink, Search, X } from "lucide-react";
import type { ReactElement } from "react";
import { useState } from "react";

import { StatusPill } from "../components/StatusPill";
import { useGetJobQuery, useListJobsQuery, useUpdateJobStatusMutation } from "../services/api";
import type { JobFilters } from "../types";

const statuses = ["", "new", "reviewing", "applied", "skipped"];
const labels = ["", "Strong Fit", "Possible Fit", "Skip"];

export function JobsPage(): ReactElement {
  const [filters, setFilters] = useState<JobFilters>({ limit: 100 });
  const [selectedJobId, setSelectedJobId] = useState<number | null>(null);
  const jobsQuery = useListJobsQuery(filters, { pollingInterval: 5_000 });
  const selectedQuery = useGetJobQuery(selectedJobId ?? 0, { skip: selectedJobId === null });
  const [updateStatus, updateState] = useUpdateJobStatusMutation();

  const selected = selectedQuery.data;

  function patchFilters(next: Partial<JobFilters>): void {
    setFilters((current) => ({ ...current, ...next }));
  }

  async function mark(status: "reviewing" | "applied" | "skipped" | "new"): Promise<void> {
    if (!selectedJobId) {
      return;
    }
    await updateStatus({ jobId: selectedJobId, status }).unwrap();
  }

  return (
    <div className="page page-split">
      <section className="list-pane">
        <header className="page-header">
          <div>
            <span className="eyebrow">Jobs</span>
            <h1>Saved Queue</h1>
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

        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Title</th>
                <th>Fit</th>
                <th>Status</th>
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
                  <td>
                    <strong>{job.title}</strong>
                    <span>{job.location || job.workday_id || "-"}</span>
                  </td>
                  <td>{job.fit_score ?? "-"} {job.fit_label || ""}</td>
                  <td>
                    <StatusPill value={job.status} />
                  </td>
                  <td>{job.recommended_resume_name || "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!jobsQuery.data?.jobs.length ? <p className="empty-state">No jobs match.</p> : null}
        </div>
      </section>

      <section className="detail-pane">
        {selected ? (
          <>
            <header className="panel-header">
              <h2>{selected.title}</h2>
              <StatusPill value={selected.status} />
            </header>
            <div className="job-meta">
              <span>{selected.workday_id || "-"}</span>
              <span>{selected.location || "-"}</span>
              <span>{selected.posting_date || "-"}</span>
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
            </div>
            <dl className="detail-list">
              <dt>Department</dt>
              <dd>{selected.department || "-"}</dd>
              <dt>Hours</dt>
              <dd>{selected.hours || "-"}</dd>
              <dt>Pay</dt>
              <dd>{selected.pay_rate || "-"}</dd>
              <dt>Resume</dt>
              <dd>{selected.recommended_resume_path || "-"}</dd>
              <dt>Notes</dt>
              <dd>{selected.application_notes || "-"}</dd>
            </dl>
            <h3>Description</h3>
            <pre className="description-box">{selected.raw_description || "No description stored."}</pre>
          </>
        ) : (
          <p className="empty-state">Select a job.</p>
        )}
      </section>
    </div>
  );
}
