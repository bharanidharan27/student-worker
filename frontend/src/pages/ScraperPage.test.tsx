import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AutomationRun, Job } from "../types";
import { ScraperPage } from "./ScraperPage";

const activeEligibilityRun = makeRun({
  id: 98,
  kind: "eligibility_review",
  params: { job_id: 101 }
});
const reviewedJob = makeJob({
  id: 101,
  title: "Research & Development Aide"
});

const apiMocks = vi.hoisted(() => ({
  startScrape: vi.fn()
}));

vi.mock("../components/RunPanel", () => ({
  RunPanel: () => <section aria-label="Scrape run" />
}));

vi.mock("../services/api", () => ({
  useGetJobQuery: () => ({
    data: reviewedJob
  }),
  useListRunsQuery: () => ({
    data: { runs: [activeEligibilityRun] }
  }),
  useStartScrapeMutation: () => [apiMocks.startScrape, { isLoading: false }]
}));

describe("ScraperPage busy warning", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("shows the active job title instead of the run id for job-specific runs", () => {
    render(<ScraperPage />);

    expect(
      screen.getByText("Browser worker is busy with eligibility review for Research & Development Aide.")
    ).toBeInTheDocument();
    expect(screen.queryByText(/#98/)).not.toBeInTheDocument();
  });
});

function makeRun(overrides: Partial<AutomationRun>): AutomationRun {
  return {
    id: 0,
    kind: "scrape",
    status: "running",
    params: {},
    result: null,
    current_step: null,
    error: null,
    started_at: null,
    finished_at: null,
    created_at: null,
    updated_at: null,
    ...overrides
  };
}

function makeJob(overrides: Partial<Job>): Job {
  return {
    id: 0,
    workday_id: null,
    title: "Untitled",
    department: null,
    location: null,
    pay_rate: null,
    hours: null,
    posting_date: null,
    deadline: null,
    url: null,
    raw_description: null,
    parsed: null,
    fit_score: null,
    fit_label: null,
    job_family: null,
    recommended_resume_type: null,
    recommended_resume_name: null,
    recommended_resume_path: null,
    eligibility_status: null,
    eligibility: null,
    eligibility_override: false,
    status: "new",
    application_notes: null,
    applied_at: null,
    last_action_at: null,
    ...overrides
  };
}
