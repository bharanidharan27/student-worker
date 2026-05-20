import { History } from "lucide-react";
import type { ReactElement } from "react";
import { useState } from "react";

import { RunPanel } from "../components/RunPanel";
import { StatusPill } from "../components/StatusPill";
import { useListRunsQuery } from "../services/api";

export function RunHistoryPage(): ReactElement {
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null);
  const runsQuery = useListRunsQuery(100, { pollingInterval: 4_000 });
  const runs = runsQuery.data?.runs ?? [];

  return (
    <div className="page page-split">
      <section className="list-pane">
        <header className="page-header">
          <div>
            <span className="eyebrow">Runs</span>
            <h1>History</h1>
          </div>
          <History size={20} aria-hidden="true" />
        </header>
        <div className="run-list">
          {runs.map((run) => (
            <button
              key={run.id}
              type="button"
              className={`run-row${selectedRunId === run.id ? " run-row--active" : ""}`}
              onClick={() => setSelectedRunId(run.id)}
            >
              <span>#{run.id}</span>
              <strong>{run.kind.replaceAll("_", " ")}</strong>
              <StatusPill value={run.status} />
              <time>{run.created_at || ""}</time>
            </button>
          ))}
          {!runs.length ? <p className="empty-state">No runs yet.</p> : null}
        </div>
      </section>
      <div className="detail-pane detail-pane--plain">
        <RunPanel runId={selectedRunId} title="Run detail" />
      </div>
    </div>
  );
}
