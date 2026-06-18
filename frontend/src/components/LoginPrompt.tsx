import { KeyRound, Loader2, ShieldCheck, X } from "lucide-react";
import type { ReactElement } from "react";
import { useState } from "react";

import {
  useCheckSessionMutation,
  useGetSessionStatusQuery,
  useStartLoginCaptureMutation
} from "../services/api";
import { RunPanel } from "./RunPanel";

interface LoginPromptProps {
  open: boolean;
  onDismiss: () => void;
}

export function LoginPrompt({ open, onDismiss }: LoginPromptProps): ReactElement | null {
  const [runId, setRunId] = useState<number | null>(null);
  const statusQuery = useGetSessionStatusQuery(undefined, { pollingInterval: 5_000 });
  const [checkSession, checkState] = useCheckSessionMutation();
  const [startLoginCapture, captureState] = useStartLoginCaptureMutation();

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
            <h2 id="login-prompt-title">Sign in to apply to jobs</h2>
            <p className="login-prompt-subtitle">
              Sign in once to enable scraping and applications. You can keep browsing saved jobs without
              signing in.
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

        {checkState.data ? (
          <p className={`notice ${checkState.data.valid ? "notice-success" : "notice-error"}`}>
            <ShieldCheck size={16} aria-hidden="true" />
            {checkState.data.message}
          </p>
        ) : null}

        <div className="login-prompt-actions">
          <button
            className="button"
            type="button"
            onClick={() => void checkSession()}
            disabled={checkState.isLoading}
            title="Verify saved session"
          >
            <ShieldCheck size={16} aria-hidden="true" />
            Check
          </button>
          <button
            className="button button-primary"
            type="button"
            onClick={() => void handleCapture()}
            disabled={captureState.isLoading}
            title="Open browser to sign in"
          >
            {captureState.isLoading ? (
              <Loader2 className="spin" size={16} aria-hidden="true" />
            ) : (
              <KeyRound size={16} aria-hidden="true" />
            )}
            Sign in with Workday
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
