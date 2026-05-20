import type { RunStatus } from "../types";

export const ACTIVE_RUN_STATUSES: RunStatus[] = ["queued", "running", "waiting_for_user"];

export function isActiveRunStatus(status: RunStatus | undefined): boolean {
  return status !== undefined && ACTIVE_RUN_STATUSES.includes(status);
}

export function runStatusTone(status: RunStatus | string | undefined): "neutral" | "good" | "warn" | "bad" {
  if (status === "completed") {
    return "good";
  }
  if (status === "waiting_for_user" || status === "queued" || status === "running") {
    return "warn";
  }
  if (status === "failed" || status === "interrupted") {
    return "bad";
  }
  return "neutral";
}
