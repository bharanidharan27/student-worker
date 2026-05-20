import { KeyRound, RefreshCw, ShieldCheck } from "lucide-react";
import type { ReactElement } from "react";
import { useState } from "react";

import { RunPanel } from "../components/RunPanel";
import {
  useCheckSessionMutation,
  useGetSessionStatusQuery,
  useStartLoginCaptureMutation
} from "../services/api";

export function SessionPage(): ReactElement {
  const [runId, setRunId] = useState<number | null>(null);
  const statusQuery = useGetSessionStatusQuery(undefined, { pollingInterval: 5_000 });
  const [checkSession, checkState] = useCheckSessionMutation();
  const [startLoginCapture, captureState] = useStartLoginCaptureMutation();

  async function handleCapture(): Promise<void> {
    const run = await startLoginCapture().unwrap();
    setRunId(run.id);
  }

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <span className="eyebrow">Session</span>
          <h1>Workday Login</h1>
        </div>
        <div className="toolbar">
          <button
            className="button"
            type="button"
            onClick={() => void checkSession()}
            disabled={checkState.isLoading}
            title="Check session"
          >
            <RefreshCw size={16} aria-hidden="true" />
            Check
          </button>
          <button
            className="button button-primary"
            type="button"
            onClick={() => void handleCapture()}
            disabled={captureState.isLoading}
            title="Capture login"
          >
            <KeyRound size={16} aria-hidden="true" />
            Capture
          </button>
        </div>
      </header>

      <section className="metrics-band">
        <div>
          <span className="label">Auth file</span>
          <strong>{statusQuery.data?.exists ? "Present" : "Missing"}</strong>
        </div>
        <div>
          <span className="label">Size</span>
          <strong>{statusQuery.data?.size_bytes ?? 0} bytes</strong>
        </div>
        <div>
          <span className="label">Modified</span>
          <strong>{statusQuery.data?.modified_at || "-"}</strong>
        </div>
      </section>

      {checkState.data ? (
        <p className={`notice ${checkState.data.valid ? "notice-success" : "notice-error"}`}>
          <ShieldCheck size={16} aria-hidden="true" />
          {checkState.data.message}
        </p>
      ) : null}

      <RunPanel runId={runId} title="Login capture" />
    </div>
  );
}
