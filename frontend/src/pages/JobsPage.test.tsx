import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Job } from "../types";
import { JobsPage } from "./JobsPage";

const jobs = [
  makeJob({ id: 101, title: "Office Aide", workday_id: "JR-101" }),
  makeJob({ id: 102, title: "Library Assistant", workday_id: "JR-102" })
];

const apiMocks = vi.hoisted(() => ({
  getJobRefetch: vi.fn(),
  listJobsRefetch: vi.fn(),
  reviewAllEligibility: vi.fn(),
  reviewJobEligibility: vi.fn(),
  tailorResume: vi.fn(),
  updateEligibilityOverride: vi.fn(),
  updateJobStatus: vi.fn()
}));

vi.mock("../services/api", () => ({
  useGetJobQuery: (jobId: number, options?: { skip?: boolean }) => ({
    data: options?.skip ? undefined : jobs.find((job) => job.id === jobId),
    isFetching: false,
    refetch: apiMocks.getJobRefetch
  }),
  useGetRunQuery: () => ({
    data: undefined
  }),
  useListJobsQuery: () => ({
    data: { jobs },
    refetch: apiMocks.listJobsRefetch
  }),
  useReviewAllEligibilityMutation: () => [apiMocks.reviewAllEligibility, { isLoading: false }],
  useReviewJobEligibilityMutation: () => [apiMocks.reviewJobEligibility, { isLoading: false }],
  useTailorResumeMutation: () => [apiMocks.tailorResume, { isLoading: false }],
  useUpdateEligibilityOverrideMutation: () => [apiMocks.updateEligibilityOverride, { isLoading: false }],
  useUpdateJobStatusMutation: () => [apiMocks.updateJobStatus, { isLoading: false }]
}));

describe("JobsPage selection", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("does not select the first job when the page initially loads", () => {
    render(<JobsPage />);

    expect(screen.getAllByText("Office Aide").length).toBeGreaterThan(0);
    expect(screen.queryByLabelText("Selected job preview")).not.toBeInTheDocument();
  });

  it("shows the selected job preview after the user chooses a job", async () => {
    render(<JobsPage />);

    const row = screen.getByText("Library Assistant").closest("tr");
    expect(row).not.toBeNull();
    fireEvent.click(row as HTMLTableRowElement);

    expect(await screen.findByLabelText("Selected job preview")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Library Assistant" })).toBeInTheDocument();
  });
});

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
