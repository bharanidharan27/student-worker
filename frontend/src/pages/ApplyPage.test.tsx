import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
  sessionStatus: {
    auth_state_path: "playwright/.auth/asu_workday.json",
    exists: false,
    authenticated: false,
    size_bytes: 0,
    modified_at: null as string | null,
    display_name: null as string | null,
    email: null as string | null
  },
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
    data: apiMocks.sessionStatus,
    isLoading: false
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
    apiMocks.sessionStatus = {
      auth_state_path: "playwright/.auth/asu_workday.json",
      exists: false,
      authenticated: false,
      size_bytes: 0,
      modified_at: null as string | null,
      display_name: null as string | null,
      email: null as string | null
    };
    apiMocks.applyJob.mockReturnValue({ unwrap: () => Promise.resolve(queuedRun("apply_job")) });
    apiMocks.applyQueue.mockReturnValue({ unwrap: () => Promise.resolve(queuedRun("apply_queue")) });
    apiMocks.checkSession.mockReturnValue({ unwrap: () => Promise.resolve({ valid: false }) });
    apiMocks.updateJobStatus.mockReturnValue({ unwrap: () => Promise.resolve({}) });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it("reopens the login prompt instead of applying a job when signed out", async () => {
    renderApplyRoute();
    expect(screen.queryByRole("button", { name: /check session/i })).not.toBeInTheDocument();
    await dismissInitialLoginPrompt();

    fireEvent.click(await screen.findByRole("button", { name: /apply job: office aide/i }));

    expect(await screen.findByRole("dialog", { name: /sign in to apply to jobs/i })).toBeInTheDocument();
    expect(apiMocks.applyJob).not.toHaveBeenCalled();
  });

  it("shows one sign-in control in the topbar when signed out", () => {
    renderApplyRoute();

    const topbar = screen.getByLabelText("Account status");

    expect(within(topbar).getByRole("button", { name: /^sign in$/i })).toBeInTheDocument();
    expect(within(topbar).queryByText(/not signed in/i)).not.toBeInTheDocument();
  });

  it("reopens the login prompt instead of applying the queue when signed out", async () => {
    renderApplyRoute();
    await dismissInitialLoginPrompt();

    fireEvent.click(screen.getByRole("button", { name: /^queue$/i }));

    expect(await screen.findByRole("dialog", { name: /sign in to apply to jobs/i })).toBeInTheDocument();
    expect(apiMocks.applyQueue).not.toHaveBeenCalled();
  });

  it("checks a saved auth file implicitly and shows a loading screen", async () => {
    apiMocks.sessionStatus = {
      auth_state_path: "playwright/.auth/asu_workday.json",
      exists: true,
      authenticated: false,
      size_bytes: 100,
      modified_at: "2026-06-20T12:00:00",
      display_name: null,
      email: null
    };
    apiMocks.checkSession.mockReturnValue({ unwrap: () => new Promise(() => undefined) });

    renderApplyRoute();

    expect(await screen.findByRole("status", { name: /checking sign-in status/i })).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: /sign in to apply to jobs/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /check session/i })).not.toBeInTheDocument();
    await waitFor(() => expect(apiMocks.checkSession).toHaveBeenCalledTimes(1));
  });

  it("keeps signed-in topbar simple and opens validation from the user menu", async () => {
    apiMocks.sessionStatus = {
      auth_state_path: "playwright/.auth/asu_workday.json",
      exists: true,
      authenticated: true,
      size_bytes: 100,
      modified_at: "2026-06-20T12:00:00",
      display_name: "Bharanidharan Maheswaran",
      email: null
    };

    renderApplyRoute();

    expect(screen.queryByRole("button", { name: /refresh session/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/refresh workday session/i)).not.toBeInTheDocument();

    fireEvent.click(await screen.findByRole("button", { name: /signed in as bharanidharan maheswaran/i }));

    expect(await screen.findByRole("menu", { name: /workday account/i })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /check sign-in status/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /refresh session/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /check session/i })).not.toBeInTheDocument();
  });

  it("checks signed-in session from the user menu with a loading screen and top alert", async () => {
    apiMocks.sessionStatus = {
      auth_state_path: "playwright/.auth/asu_workday.json",
      exists: true,
      authenticated: true,
      size_bytes: 100,
      modified_at: "2026-06-20T12:00:00",
      display_name: "Bharanidharan Maheswaran",
      email: null
    };
    let resolveCheck: ((value: typeof apiMocks.sessionStatus & { valid: boolean; message: string }) => void) | undefined;
    apiMocks.checkSession.mockReturnValue({
      unwrap: () =>
        new Promise((resolve) => {
          resolveCheck = resolve;
        })
    });

    renderApplyRoute();
    fireEvent.click(await screen.findByRole("button", { name: /signed in as bharanidharan maheswaran/i }));
    fireEvent.click(await screen.findByRole("menuitem", { name: /check sign-in status/i }));

    expect(await screen.findByRole("status", { name: /checking sign-in status/i })).toBeInTheDocument();
    expect(screen.queryByRole("menu", { name: /workday account/i })).not.toBeInTheDocument();

    resolveCheck?.({
      ...apiMocks.sessionStatus,
      valid: true,
      message: "Saved Workday session looks valid."
    });

    expect(await screen.findByRole("alert")).toHaveTextContent(/sign-in is valid/i);
    expect(screen.queryByRole("status", { name: /checking sign-in status/i })).not.toBeInTheDocument();
    expect(apiMocks.checkSession).toHaveBeenCalledTimes(1);
  });

  it("moves the user back to sign-in when the signed-in session is no longer valid", async () => {
    apiMocks.sessionStatus = {
      auth_state_path: "playwright/.auth/asu_workday.json",
      exists: true,
      authenticated: true,
      size_bytes: 100,
      modified_at: "2026-06-20T12:00:00",
      display_name: "Bharanidharan Maheswaran",
      email: null
    };
    apiMocks.checkSession.mockReturnValue({
      unwrap: () =>
        Promise.resolve({
          ...apiMocks.sessionStatus,
          authenticated: false,
          valid: false,
          message: "Session expired."
        })
    });

    renderApplyRoute();
    fireEvent.click(await screen.findByRole("button", { name: /signed in as bharanidharan maheswaran/i }));
    fireEvent.click(await screen.findByRole("menuitem", { name: /check sign-in status/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/sign-in needs attention/i);
    expect(within(screen.getByLabelText("Account status")).getByRole("button", { name: /^sign in$/i })).toBeInTheDocument();
    expect(within(screen.getByLabelText("Account status")).queryByText(/not signed in/i)).not.toBeInTheDocument();
    expect(await screen.findByRole("dialog", { name: /sign in to apply to jobs/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /signed in as bharanidharan maheswaran/i })).not.toBeInTheDocument();
  });
});
