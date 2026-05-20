export type RunStatus =
  | "queued"
  | "running"
  | "waiting_for_user"
  | "completed"
  | "failed"
  | "interrupted";

export interface AutomationRun {
  id: number;
  kind: string;
  status: RunStatus;
  params: Record<string, unknown>;
  result: Record<string, unknown> | unknown[] | null;
  current_step: string | null;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface AutomationRunLog {
  id: number;
  run_id: number;
  level: string;
  message: string;
  created_at: string | null;
}

export interface SessionStatus {
  auth_state_path: string;
  exists: boolean;
  size_bytes: number;
  modified_at: string | null;
}

export interface SessionCheck extends SessionStatus {
  valid: boolean;
  message: string;
}

export interface Job {
  id: number;
  workday_id: string | null;
  title: string;
  department: string | null;
  location: string | null;
  pay_rate: string | null;
  hours: string | null;
  posting_date: string | null;
  deadline: string | null;
  url: string | null;
  raw_description: string | null;
  parsed: Record<string, unknown> | null;
  fit_score: number | null;
  fit_label: string | null;
  job_family: string | null;
  recommended_resume_type: string | null;
  recommended_resume_name: string | null;
  recommended_resume_path: string | null;
  status: string | null;
  application_notes: string | null;
  applied_at: string | null;
  last_action_at: string | null;
}

export interface JobFilters {
  q?: string;
  status?: string;
  fit_label?: string;
  min_score?: number;
  posted_from?: string;
  posted_to?: string;
  queue?: boolean;
  limit?: number;
}

export interface ScrapeRequest {
  limit?: number | null;
  headed: boolean;
  wait_ms: number;
  max_scrolls: number;
  idle_rounds: number;
  click_timeout_ms: number;
  debug_dump_dir?: string | null;
}

export interface ApplyRequest {
  submit: boolean;
  confirm_submit: boolean;
  headed: boolean;
  limit?: number;
  min_score?: number;
  fit_label?: string;
  click_timeout_ms: number;
  applicant_name: string;
}
