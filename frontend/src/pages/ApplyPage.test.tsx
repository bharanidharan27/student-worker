import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Shell } from "../components/Shell";
import { ApplyPage } from "./ApplyPage";

const apiMocks = vi.hoisted(() => ({
  applyJob: vi.fn(),
  applyQueue: vi.fn(),
  checkSession: vi.fn(),
  continueRun: vi.fn(),
  getRunEventsRefetch: vi.fn(),
  getRunRefetch: vi.fn(),
  refetchQueue: vi.fn(),
  startLoginCapture: vi.fn(),
  stopRun: vi.fn(),
  updateJobStatus: vi.fn()
}));

vi.mock("../services/api", () => ({
  useApplyJobMutation: () => [apiMocks.applyJob, { isLoading: false }],
  useApplyQueueMutation: () => [apiMocks.applyQueue, { isLoading: false }],
  useCheckSessionMutation: () => [apiMocks.checkSession, { data: undefined, isLoading: false }],
  useContinueRunMutation: () => [apiMocks.continueRun, { isLoading: false }],
  useGetRunEventsQuery: () => ({
    data: { events: [] },
    isFetching: false,
    refetch: apiMocks.getRunEventsRefetch
  }),
  useGetRunQuery: () => ({
    data: undefined,
    isFetching: false,
    refetch: apiMocks.getRunRefetch
  }),
  useGetSessionStatusQuery: () => ({
    data: {
      auth_state_path: "playwright/.auth/asu_workday.json",
      exists: false,
      authenticated: false,
      size_bytes: 0,
      modified_at: null,
      display_name: null,
      email: null
    }
  }),
  useListJobsQuery: () => ({
    data: {
      jobs: [
        {
          id: 101,
          workday_id: "JR-101",
          title: "Office Aide",
          department: "Student Services",
          location: "Tempe",
          pay_rate: null,
          hours: null,
          posting_date: null,
          deadline: null,
          url: null,
          raw_description: null,
          parsed: null,
          fit_score: 88,
          fit_label: "Strong Fit",
          job_family: null,
          recommended_resume_type: null,
          recommended_resume_name: "Office Resume.pdf",
          recommended_resume_path: null,
          eligibility_status: "eligible",
          eligibility: null,
          eligibility_override: false,
          status: "new",
          application_notes: null,
          applied_at: null,
          last_action_at: null
        }
      ]
    },
    refetch: apiMocks.refetchQueue
  }),
  useListRunsQuery: () => ({ data: { runs: [] } }),
  useStartLoginCaptureMutation: () => [apiMocks.startLoginCapture, { isLoading: false }],
  useStopRunMutation: () => [apiMocks.stopRun, { isLoading: false }],
  useUpdateJobStatusMutation: () => [apiMocks.updateJobStatus, { isLoading: false }]
}));

function renderApplyRoute(): void {
  render(
    <MemoryRouter initialEntries={["/apply"]}>
      <Routes>
        <Route element={<Shell />}>
          <Route path="/apply" element={<ApplyPage />} />
        </Route>
      </Routes>
    </MemoryRouter>
  );
}

async function dismissInitialLoginPrompt(): Promise<void> {
  const continueButton = await screen.findByRole("button", { name: /continue browsing/i });
  fireEvent.click(continueButton);
  await waitFor(() => {
    expect(screen.queryByRole("dialog", { name: /sign in to apply to jobs/i })).not.toBeInTheDocument();
  });
}

function queuedRun(kind: "apply_job" | "apply_queue") {
  return {
    id: kind === "apply_job" ? 12 : 13,
    kind,
    status: "queued",
    params: kind === "apply_job" ? { job_id: 101 } : {},
    result: null,
    current_step: null,
    error: null,
    started_at: null,
    finished_at: null,
    created_at: null,
    updated_at: null
  };
}

describe("ApplyPage auth prompts", () => {
  beforeEach(() => {
    apiMocks.applyJob.mockReturnValue({ unwrap: () => Promise.resolve(queuedRun("apply_job")) });
    apiMocks.applyQueue.mockReturnValue({ unwrap: () => Promise.resolve(queuedRun("apply_queue")) });
    apiMocks.updateJobStatus.mockReturnValue({ unwrap: () => Promise.resolve({}) });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("reopens the login prompt instead of applying a job when signed out", async () => {
    renderApplyRoute();
    await dismissInitialLoginPrompt();

    fireEvent.click(await screen.findByRole("button", { name: /apply job: office aide/i }));

    expect(await screen.findByRole("dialog", { name: /sign in to apply to jobs/i })).toBeInTheDocument();
    expect(apiMocks.applyJob).not.toHaveBeenCalled();
  });

  it("reopens the login prompt instead of applying the queue when signed out", async () => {
    renderApplyRoute();
    await dismissInitialLoginPrompt();

    fireEvent.click(screen.getByRole("button", { name: /^queue$/i }));

    expect(await screen.findByRole("dialog", { name: /sign in to apply to jobs/i })).toBeInTheDocument();
    expect(apiMocks.applyQueue).not.toHaveBeenCalled();
  });
});
