import { KeyRound, Loader2, X } from "lucide-react";
import type { ReactElement } from "react";
import { useState } from "react";

import {
  useGetSessionStatusQuery,
  useStartLoginCaptureMutation
} from "../services/api";
import { RunPanel } from "./RunPanel";

interface LoginPromptProps {
  checkingSession: boolean;
  open: boolean;
  onDismiss: () => void;
}

export function LoginPrompt({ checkingSession, open, onDismiss }: LoginPromptProps): ReactElement | null {
  const [runId, setRunId] = useState<number | null>(null);
  const statusQuery = useGetSessionStatusQuery(undefined, { pollingInterval: 5_000 });
  const [startLoginCapture, captureState] = useStartLoginCaptureMutation();
  const signedIn = Boolean(statusQuery.data?.authenticated);
  const profileLabel = statusQuery.data?.display_name?.trim() || statusQuery.data?.email?.trim() || "your Workday account";

  if (!open) {
    return null;
  }

  async function handleCapture(): Promise<void> {
    const run = await startLoginCapture().unwrap();
    setRunId(run.id);
  }

  return (
    <div className="login-prompt-backdrop" role="dialog" aria-modal="true" aria-labelledby="login-prompt-title">
      <div className="login-prompt">
        <button
          className="icon-button login-prompt-close"
          type="button"
          onClick={onDismiss}
          aria-label="Close login prompt"
          title="Continue browsing without signing in"
        >
          <X size={18} aria-hidden="true" />
        </button>
        <header className="login-prompt-header">
          <div className="login-prompt-icon" aria-hidden="true">
            <KeyRound size={22} />
          </div>
          <div>
            <span className="eyebrow">Workday sign in</span>
            <h2 id="login-prompt-title">{signedIn ? "Refresh Workday session" : "Sign in to apply to jobs"}</h2>
            <p className="login-prompt-subtitle">
              {signedIn
                ? `Signed in as ${profileLabel}. Refresh the session if Workday asks you to sign in again.`
                : "Sign in once to enable scraping and applications. You can keep browsing saved jobs without signing in."}
            </p>
          </div>
        </header>

        <section className="login-prompt-status">
          <div>
            <span className="label">Auth file</span>
            <strong>{statusQuery.data?.exists ? "Present" : "Missing"}</strong>
          </div>
          <div>
            <span className="label">Size</span>
            <strong>{statusQuery.data?.size_bytes ?? 0} bytes</strong>
          </div>
          <div>
            <span className="label">Last sign in</span>
            <strong>{statusQuery.data?.modified_at || "-"}</strong>
          </div>
        </section>

        {checkingSession ? (
          <p className="notice notice-info">
            <Loader2 className="spin" size={16} aria-hidden="true" />
            Checking session
          </p>
        ) : null}

        <div className="login-prompt-actions">
          <button
            className="button button-primary"
            type="button"
            onClick={() => void handleCapture()}
            disabled={captureState.isLoading || checkingSession}
            title={checkingSession ? "Checking Workday session" : signedIn ? "Refresh Workday session" : "Open browser to sign in"}
          >
            {captureState.isLoading || checkingSession ? (
              <Loader2 className="spin" size={16} aria-hidden="true" />
            ) : (
              <KeyRound size={16} aria-hidden="true" />
            )}
            {checkingSession ? "Loading" : signedIn ? "Refresh Workday session" : "Sign in with Workday"}
          </button>
          <button
            className="button button-ghost"
            type="button"
            onClick={onDismiss}
            title="Browse jobs without signing in"
          >
            Continue browsing
          </button>
        </div>

        <RunPanel runId={runId} title="Login capture" compact />
      </div>
    </div>
  );
}
