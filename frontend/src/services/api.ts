import { createApi, fetchBaseQuery } from "@reduxjs/toolkit/query/react";

import type {
  ApplyRequest,
  AutomationRun,
  AutomationRunLog,
  Job,
  JobFilters,
  ScrapeRequest,
  SessionCheck,
  SessionStatus
} from "../types";

interface RunListResponse {
  runs: AutomationRun[];
}

interface RunEventsResponse {
  events: AutomationRunLog[];
}

interface JobsResponse {
  jobs: Job[];
}

interface ContinueResponse {
  accepted: boolean;
  run: AutomationRun;
}

export const consoleApi = createApi({
  reducerPath: "consoleApi",
  baseQuery: fetchBaseQuery({ baseUrl: "/api" }),
  tagTypes: ["Run", "Job", "Session"],
  endpoints: (builder) => ({
    getSessionStatus: builder.query<SessionStatus, void>({
      query: () => "/session/status",
      providesTags: ["Session"]
    }),
    checkSession: builder.mutation<SessionCheck, void>({
      query: () => ({ url: "/session/check", method: "POST" }),
      invalidatesTags: ["Session"]
    }),
    startLoginCapture: builder.mutation<AutomationRun, void>({
      query: () => ({ url: "/session/capture/start", method: "POST", body: {} }),
      invalidatesTags: ["Run", "Session"]
    }),
    listRuns: builder.query<RunListResponse, number | void>({
      query: (limit = 50) => `/runs?limit=${limit}`,
      providesTags: ["Run"]
    }),
    getRun: builder.query<AutomationRun, number>({
      query: (runId) => `/runs/${runId}`,
      providesTags: (_result, _error, runId) => [{ type: "Run", id: runId }]
    }),
    getRunEvents: builder.query<RunEventsResponse, number>({
      query: (runId) => `/runs/${runId}/events`,
      providesTags: (_result, _error, runId) => [{ type: "Run", id: `${runId}:events` }]
    }),
    continueRun: builder.mutation<ContinueResponse, number>({
      query: (runId) => ({ url: `/runs/${runId}/continue`, method: "POST" }),
      invalidatesTags: (_result, _error, runId) => [
        { type: "Run", id: runId },
        { type: "Run", id: `${runId}:events` },
        "Run"
      ]
    }),
    startScrape: builder.mutation<AutomationRun, ScrapeRequest>({
      query: (body) => ({ url: "/scrapes", method: "POST", body }),
      invalidatesTags: ["Run", "Job"]
    }),
    listJobs: builder.query<JobsResponse, JobFilters | void>({
      query: (filters) => {
        const normalizedFilters = filters ?? {};
        const params = new URLSearchParams();
        for (const [key, value] of Object.entries(normalizedFilters)) {
          if (value !== undefined && value !== "" && value !== null) {
            params.set(key, String(value));
          }
        }
        const suffix = params.toString();
        return suffix ? `/jobs?${suffix}` : "/jobs";
      },
      providesTags: ["Job"]
    }),
    getJob: builder.query<Job, number>({
      query: (jobId) => `/jobs/${jobId}`,
      providesTags: (_result, _error, jobId) => [{ type: "Job", id: jobId }]
    }),
    updateJobStatus: builder.mutation<Job, { jobId: number; status: string; note?: string }>({
      query: ({ jobId, status, note }) => ({
        url: `/jobs/${jobId}/status`,
        method: "PATCH",
        body: { status, note }
      }),
      invalidatesTags: (_result, _error, { jobId }) => ["Job", { type: "Job", id: jobId }]
    }),
    applyJob: builder.mutation<AutomationRun, { jobId: number; body: ApplyRequest }>({
      query: ({ jobId, body }) => ({
        url: `/apply/job/${jobId}`,
        method: "POST",
        body
      }),
      invalidatesTags: ["Run", "Job"]
    }),
    applyQueue: builder.mutation<AutomationRun, ApplyRequest>({
      query: (body) => ({ url: "/apply/queue", method: "POST", body }),
      invalidatesTags: ["Run", "Job"]
    })
  })
});

export const {
  useApplyJobMutation,
  useApplyQueueMutation,
  useCheckSessionMutation,
  useContinueRunMutation,
  useGetJobQuery,
  useGetRunEventsQuery,
  useGetRunQuery,
  useGetSessionStatusQuery,
  useListJobsQuery,
  useListRunsQuery,
  useStartLoginCaptureMutation,
  useStartScrapeMutation,
  useUpdateJobStatusMutation
} = consoleApi;
