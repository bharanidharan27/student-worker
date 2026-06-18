import { skipToken } from "@reduxjs/toolkit/query";
import {
  AlertTriangle,
  Check,
  ExternalLink,
  FilePenLine,
  Loader2,
  Lock,
  RotateCw,
  Search,
  Undo2,
  Unlock,
  X
} from "lucide-react";
import type { ReactElement } from "react";
import { useEffect, useState } from "react";

import { RunPanel } from "../components/RunPanel";
import { StatusPill } from "../components/StatusPill";
import {
  useGetJobQuery,
  useGetRunQuery,
  useListJobsQuery,
  useReviewAllEligibilityMutation,
  useReviewJobEligibilityMutation,
  useUpdateEligibilityOverrideMutation,
  useTailorResumeMutation,
  useUpdateJobStatusMutation
} from "../services/api";
import type { EligibilityAssessment, JobFilters, JobRequirement, JobSort } from "../types";
import { isActiveRunStatus } from "../utils/runStatus";

const statuses = ["", "new", "reviewing", "applied", "skipped"];
const labels = ["", "Strong Fit", "Possible Fit", "Skip"];
const eligibilityStatuses = ["", "eligible", "needs_review", "ineligible"];
const requirementMatches = ["missing", "unknown", "met", "not_applicable"];
const sortOptions: Array<{ label: string; value: JobSort }> = [
  { label: "Best fit", value: "best_fit" },
  { label: "Extracted order", value: "extracted" },
  { label: "Posted newest", value: "posted_desc" },
  { label: "Posted oldest", value: "posted_asc" }
];

export function JobsPage(): ReactElement {
  const [filters, setFilters] = useState<JobFilters>({ limit: 100, sort: "best_fit" });
  const [selectedJobId, setSelectedJobId] = useState<number | null>(null);
  const [selectionClosed, setSelectionClosed] = useState(false);
  const [eligibilityRunId, setEligibilityRunId] = useState<number | null>(null);
  const [resumeRunId, setResumeRunId] = useState<number | null>(null);
  const jobsQuery = useListJobsQuery(filters, { pollingInterval: 5_000 });
  const selectedQuery = useGetJobQuery(selectedJobId ?? 0, { skip: selectedJobId === null });
  const eligibilityRunQuery = useGetRunQuery(eligibilityRunId ?? skipToken, {
    pollingInterval: eligibilityRunId ? 2_000 : 0
  });
  const resumeRunQuery = useGetRunQuery(resumeRunId ?? skipToken, {
    pollingInterval: resumeRunId ? 2_000 : 0
  });
  const [updateStatus, updateState] = useUpdateJobStatusMutation();
  const [updateEligibilityOverride, overrideState] = useUpdateEligibilityOverrideMutation();
  const [reviewJobEligibility, reviewJobState] = useReviewJobEligibilityMutation();
  const [reviewAllEligibility, reviewAllState] = useReviewAllEligibilityMutation();
  const [tailorResume, tailorResumeState] = useTailorResumeMutation();

  const selected = selectedQuery.data;
  const jobs = jobsQuery.data?.jobs ?? [];
  const eligibilityRunActive = isActiveRunStatus(eligibilityRunQuery.data?.status);
  const resumeRunActive = isActiveRunStatus(resumeRunQuery.data?.status);

  useEffect(() => {
    if (jobs.length === 0 && selectedJobId !== null) {
      setSelectedJobId(null);
      return;
    }
    if (selectedJobId === null && jobs.length > 0 && !selectionClosed) {
      setSelectedJobId(jobs[0].id);
    }
    if (selectedJobId !== null && jobs.length > 0 && !jobs.some((job) => job.id === selectedJobId)) {
      setSelectedJobId(jobs[0].id);
    }
  }, [jobs, selectedJobId, selectionClosed]);

  useEffect(() => {
    if (!eligibilityRunQuery.data || isActiveRunStatus(eligibilityRunQuery.data.status)) {
      return;
    }
    void jobsQuery.refetch();
    if (selectedJobId !== null) {
      void selectedQuery.refetch();
    }
  }, [eligibilityRunQuery.data, jobsQuery, selectedJobId, selectedQuery]);

  function patchFilters(next: Partial<JobFilters>): void {
    setFilters((current) => ({ ...current, ...next }));
  }

  function clearDateFilters(): void {
    patchFilters({ posted_from: undefined, posted_to: undefined });
  }

  function selectJob(jobId: number): void {
    setSelectionClosed(false);
    setSelectedJobId(jobId);
  }

  function closeSelectedJob(): void {
    setSelectionClosed(true);
    setSelectedJobId(null);
  }

  async function mark(status: "reviewing" | "applied" | "skipped" | "new", note?: string): Promise<void> {
    if (!selectedJobId) {
      return;
    }
    await updateStatus({ jobId: selectedJobId, status, note }).unwrap();
  }

  async function toggleEligibilityOverride(): Promise<void> {
    if (!selected) {
      return;
    }
    const next = !selected.eligibility_override;
    await updateEligibilityOverride({
      jobId: selected.id,
      eligibility_override: next,
      note: next ? "Eligibility override enabled from Jobs page." : "Eligibility override cleared from Jobs page."
    }).unwrap();
  }

  async function startAllEligibilityReview(): Promise<void> {
    const run = await reviewAllEligibility().unwrap();
    setEligibilityRunId(run.id);
  }

  async function startSelectedEligibilityReview(): Promise<void> {
    if (!selected) {
      return;
    }
    const run = await reviewJobEligibility(selected.id).unwrap();
    setEligibilityRunId(run.id);
  }

  async function startTailorResume(): Promise<void> {
    if (!selected) {
      return;
    }
    const run = await tailorResume(selected.id).unwrap();
    setResumeRunId(run.id);
  }

  return (
    <div className="page page-jobs">
      <section className="list-pane jobs-list-pane">
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
              onClick={() => void startAllEligibilityReview()}
              disabled={reviewAllState.isLoading || eligibilityRunActive}
              title="Review eligibility for all saved jobs"
            >
              {reviewAllState.isLoading || eligibilityRunActive ? (
                <Loader2 className="spin" size={16} aria-hidden="true" />
              ) : (
                <RotateCw size={16} aria-hidden="true" />
              )}
              Review All
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
          <select
            value={filters.eligibility_status || ""}
            onChange={(event) => patchFilters({ eligibility_status: event.target.value })}
          >
            {eligibilityStatuses.map((value) => (
              <option key={value || "all"} value={value}>
                {value ? formatEligibility(value) : "All eligibility"}
              </option>
            ))}
          </select>
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
              <col className="jobs-col-eligibility" />
              <col className="jobs-col-status" />
              <col className="jobs-col-applied" />
              <col className="jobs-col-resume" />
            </colgroup>
            <thead>
              <tr>
                <th>Title</th>
                <th>Fit</th>
                <th>Eligibility</th>
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
                  onClick={() => selectJob(job.id)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      selectJob(job.id);
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
                  <td className="eligibility-cell">
                    <span className={`eligibility-badge eligibility-badge--${job.eligibility_status || "none"}`}>
                      {formatEligibility(job.eligibility_status)}
                    </span>
                    <span>{eligibilitySnapshot(job.eligibility, job.eligibility_override)}</span>
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

      {selectedJobId !== null ? (
        <section className="detail-pane detail-pane--drawer" aria-label="Selected job preview">
          {selectedQuery.isFetching && !selected ? (
            <p className="empty-state empty-state-panel">Loading job detail.</p>
          ) : selected ? (
            <>
              <header className="panel-header">
                <div>
                  <span className="eyebrow">Selected Job</span>
                  <h2>{selected.title}</h2>
                </div>
                <div className="panel-header-actions">
                  <StatusPill value={selected.status} />
                  <button
                    type="button"
                    className="icon-button"
                    onClick={closeSelectedJob}
                    aria-label="Close selected job"
                    title="Close selected job"
                  >
                    <X size={16} aria-hidden="true" />
                  </button>
                </div>
              </header>
              <div className="job-meta">
                <span>{selected.workday_id || "-"}</span>
                <span>{selected.location || "-"}</span>
                <span>{selected.posting_date || "-"}</span>
                <span>Applied {formatAppliedAt(selected.applied_at)}</span>
                <span>{selected.fit_score ?? "-"} / 100</span>
                <span>{formatEligibility(selected.eligibility_status)}</span>
                {selected.eligibility_override ? <span>Override</span> : null}
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
                <button
                  type="button"
                  className="button"
                  onClick={() => void startTailorResume()}
                  disabled={tailorResumeState.isLoading || resumeRunActive || !selected.recommended_resume_name}
                  title="Create a tailored resume copy from the extracted source"
                >
                  {tailorResumeState.isLoading || resumeRunActive ? (
                    <Loader2 className="spin" size={16} aria-hidden="true" />
                  ) : (
                    <FilePenLine size={16} aria-hidden="true" />
                  )}
                  Tailor Resume
                </button>
                <button
                  type="button"
                  className="button"
                  onClick={() => void startSelectedEligibilityReview()}
                  disabled={reviewJobState.isLoading || eligibilityRunActive}
                  title="Re-review eligibility for selected job"
                >
                  {reviewJobState.isLoading || eligibilityRunActive ? (
                    <Loader2 className="spin" size={16} aria-hidden="true" />
                  ) : (
                    <RotateCw size={16} aria-hidden="true" />
                  )}
                  Eligibility
                </button>
                {selected.eligibility_status === "ineligible" || selected.eligibility_override ? (
                  <button
                    type="button"
                    className="button"
                    onClick={() => void toggleEligibilityOverride()}
                    disabled={overrideState.isLoading}
                    title={selected.eligibility_override ? "Clear eligibility override" : "Allow apply despite eligibility review"}
                  >
                    {selected.eligibility_override ? <Lock size={16} aria-hidden="true" /> : <Unlock size={16} aria-hidden="true" />}
                    {selected.eligibility_override ? "Block" : "Allow"}
                  </button>
                ) : null}
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
                <dt>Applied</dt>
                <dd>{formatAppliedAt(selected.applied_at)}</dd>
                <dt>Resume</dt>
                <dd>{selected.recommended_resume_path || "-"}</dd>
                <dt>Notes</dt>
                <dd>{selected.application_notes || "-"}</dd>
              </dl>
              <EligibilityPanel eligibility={selected.eligibility} override={selected.eligibility_override} />
              <h3>Description</h3>
              <pre className="description-box">{selected.raw_description || "No description stored."}</pre>
            </>
          ) : (
            <p className="empty-state empty-state-panel">Select a job.</p>
          )}
        </section>
      ) : null}
      {eligibilityRunId ? (
        <div className="run-panel-wide">
          <RunPanel runId={eligibilityRunId} title="Eligibility run" />
        </div>
      ) : null}
      {resumeRunId ? (
        <div className="run-panel-wide">
          <RunPanel runId={resumeRunId} title="Resume run" />
        </div>
      ) : null}
    </div>
  );
}

interface EligibilityPanelProps {
  eligibility: EligibilityAssessment | null;
  override: boolean;
}

function EligibilityPanel({ eligibility, override }: EligibilityPanelProps): ReactElement {
  if (!eligibility) {
    return (
      <div className="eligibility-box">
        <h3>Eligibility</h3>
        <p className="empty-state">Not reviewed yet.</p>
      </div>
    );
  }

  const missingCount = countRequirementsByMatch(eligibility.requirements, "missing");
  const unknownCount = countRequirementsByMatch(eligibility.requirements, "unknown");
  const metCount = countRequirementsByMatch(eligibility.requirements, "met");
  const visibleWarnings = filterVisibleWarnings(eligibility);
  const hasReviewItems =
    eligibility.blockers.length > 0 ||
    visibleWarnings.length > 0 ||
    eligibility.resume_suggestions.length > 0 ||
    eligibility.non_resume_actions.length > 0;

  return (
    <div className="eligibility-box">
      <header className="section-header">
        <div>
          <h3>Eligibility</h3>
          <span className="section-subtitle">
            {eligibility.llm_used ? `${eligibility.provider || "LLM"} review` : "Local rules review"}
          </span>
        </div>
        <span className={`eligibility-badge eligibility-badge--${eligibility.status}`}>
          {formatEligibility(eligibility.status)}
        </span>
      </header>
      {override ? (
        <p className="notice notice-warn">
          <AlertTriangle size={16} aria-hidden="true" />
          Manual override is enabled for this job.
        </p>
      ) : null}
      <div className="eligibility-overview">
        <p className="eligibility-summary">{eligibility.summary}</p>
        <div className="eligibility-metrics" aria-label="Eligibility requirement counts">
          <span className="eligibility-metric eligibility-metric--bad">
            <strong>{missingCount}</strong>
            Missing
          </span>
          <span className="eligibility-metric eligibility-metric--warn">
            <strong>{unknownCount}</strong>
            Unknown
          </span>
          <span className="eligibility-metric eligibility-metric--good">
            <strong>{metCount}</strong>
            Met
          </span>
          <span className="eligibility-metric">
            <strong>{eligibility.requirements.length}</strong>
            Total
          </span>
        </div>
      </div>
      {hasReviewItems ? (
        <div className="eligibility-review-grid">
          <CompactList title="Blockers" values={eligibility.blockers} tone="bad" />
          <CompactList title="Warnings" values={visibleWarnings} tone="warn" />
          <CompactList
            title="Resume Changes"
            values={eligibility.resume_suggestions.map((item) => `${item.suggestion} Evidence: ${item.evidence}`)}
            tone="neutral"
          />
          <CompactList
            title="Non-Resume Actions"
            values={eligibility.non_resume_actions.map((item) => item.description)}
            tone="neutral"
          />
        </div>
      ) : null}
      <RequirementsByMatch requirements={eligibility.requirements} />
    </div>
  );
}

interface CompactListProps {
  tone?: "bad" | "neutral" | "warn";
  title: string;
  values: string[];
}

function CompactList({ title, values, tone = "neutral" }: CompactListProps): ReactElement | null {
  if (!values.length) {
    return null;
  }
  return (
    <section className={`compact-list compact-list--${tone}`}>
      <h4>{title}</h4>
      <ul>
        {values.map((value) => (
          <li key={value}>{value}</li>
        ))}
      </ul>
    </section>
  );
}

interface RequirementsByMatchProps {
  requirements: JobRequirement[];
}

function RequirementsByMatch({ requirements }: RequirementsByMatchProps): ReactElement | null {
  if (!requirements.length) {
    return null;
  }
  const grouped = groupRequirements(requirements);
  return (
    <section className="requirements-panel">
      <header className="section-header">
        <div>
          <h3>Requirements</h3>
          <span className="section-subtitle">{requirements.length} extracted</span>
        </div>
      </header>
      <div className="requirements-groups">
        {requirementMatches.map((match) => {
          const items = grouped[match] ?? [];
          if (!items.length) {
            return null;
          }
          return (
            <details
              key={match}
              className={`requirement-group requirement-group--${match}`}
              open={match === "missing" || match === "unknown"}
            >
              <summary>
                <span>{formatRequirementMatch(match)}</span>
                <strong>{items.length}</strong>
              </summary>
              <div className="requirement-card-list">
                {items.map((requirement, index) => (
                  <details className="requirement-card" key={`${requirement.match}-${requirement.text}-${index}`}>
                    <summary>
                      <span className="requirement-title">{requirement.text}</span>
                      <span className="requirement-card-header">
                        <span>{requirement.priority}</span>
                        <span>{requirement.category}</span>
                      </span>
                    </summary>
                    <div className="requirement-card-body">
                      {requirement.source_quote ? <blockquote>{requirement.source_quote}</blockquote> : null}
                      {requirement.evidence.length ? (
                        <small>{requirement.evidence.join(" ")}</small>
                      ) : requirement.notes ? (
                        <small>{requirement.notes}</small>
                      ) : null}
                    </div>
                  </details>
                ))}
              </div>
            </details>
          );
        })}
      </div>
    </section>
  );
}

function groupRequirements(requirements: JobRequirement[]): Record<string, JobRequirement[]> {
  return requirements.reduce<Record<string, JobRequirement[]>>((grouped, requirement) => {
    const key = requirement.match || "unknown";
    grouped[key] = grouped[key] ?? [];
    grouped[key].push(requirement);
    return grouped;
  }, {});
}

function countRequirementsByMatch(requirements: JobRequirement[], match: string): number {
  return requirements.filter((requirement) => requirement.match === match).length;
}

function filterVisibleWarnings(eligibility: EligibilityAssessment): string[] {
  const duplicateRequirementTexts = eligibility.requirements
    .filter((requirement) => requirement.match === "missing" || requirement.match === "unknown")
    .map((requirement) => normalizeReviewText(requirement.text))
    .filter((value) => value.length > 8);

  return eligibility.warnings.filter((warning) => {
    const normalized = normalizeReviewText(warning.replace(/^preferred:\s*/i, ""));
    if (warning.trim().toLowerCase().startsWith("preferred:")) {
      return false;
    }
    return !duplicateRequirementTexts.some(
      (requirementText) => normalized.includes(requirementText) || requirementText.includes(normalized)
    );
  });
}

function normalizeReviewText(value: string): string {
  return value
    .toLowerCase()
    .replaceAll(/[^a-z0-9]+/g, " ")
    .trim();
}

function eligibilitySnapshot(eligibility: EligibilityAssessment | null, override: boolean): string {
  if (override) {
    return "manual override";
  }
  if (!eligibility) {
    return "-";
  }
  const missing = eligibility.requirements.filter((requirement) => requirement.match === "missing").length;
  const unknown = eligibility.requirements.filter((requirement) => requirement.match === "unknown").length;
  if (missing || unknown) {
    return [missing ? `${missing} missing` : "", unknown ? `${unknown} unknown` : ""].filter(Boolean).join(" | ");
  }
  return `${eligibility.requirements.length} checked`;
}

function formatRequirementMatch(value: string): string {
  return value.replaceAll("_", " ");
}

function formatEligibility(value: string | null): string {
  if (!value) {
    return "Not reviewed";
  }
  return value.replaceAll("_", " ");
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
