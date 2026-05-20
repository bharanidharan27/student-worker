import { Play, Radar } from "lucide-react";
import type { ReactElement } from "react";
import { useState } from "react";

import { RunPanel } from "../components/RunPanel";
import { useStartScrapeMutation } from "../services/api";
import type { ScrapeRequest } from "../types";

export function ScraperPage(): ReactElement {
  const [runId, setRunId] = useState<number | null>(null);
  const [form, setForm] = useState<ScrapeRequest>({
    limit: 10,
    headed: false,
    wait_ms: 750,
    max_scrolls: 50,
    idle_rounds: 3,
    click_timeout_ms: 5_000,
    debug_dump_dir: "outputs/debug"
  });
  const [startScrape, startState] = useStartScrapeMutation();

  async function handleStart(): Promise<void> {
    const run = await startScrape(form).unwrap();
    setRunId(run.id);
  }

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <span className="eyebrow">Scraper</span>
          <h1>Workday Extraction</h1>
        </div>
        <button
          className="button button-primary"
          type="button"
          onClick={() => void handleStart()}
          disabled={startState.isLoading}
          title="Start scrape"
        >
          <Play size={16} aria-hidden="true" />
          Start
        </button>
      </header>

      <section className="panel">
        <header className="panel-header">
          <h2>Settings</h2>
          <Radar size={18} aria-hidden="true" />
        </header>
        <div className="form-grid">
          <label>
            <span>Limit</span>
            <input
              type="number"
              min={1}
              max={500}
              value={form.limit ?? ""}
              onChange={(event) => setForm({ ...form, limit: Number(event.target.value) || null })}
            />
          </label>
          <label>
            <span>Max scrolls</span>
            <input
              type="number"
              min={1}
              max={250}
              value={form.max_scrolls}
              onChange={(event) => setForm({ ...form, max_scrolls: Number(event.target.value) })}
            />
          </label>
          <label>
            <span>Idle rounds</span>
            <input
              type="number"
              min={1}
              max={25}
              value={form.idle_rounds}
              onChange={(event) => setForm({ ...form, idle_rounds: Number(event.target.value) })}
            />
          </label>
          <label>
            <span>Wait ms</span>
            <input
              type="number"
              min={0}
              max={10_000}
              value={form.wait_ms}
              onChange={(event) => setForm({ ...form, wait_ms: Number(event.target.value) })}
            />
          </label>
          <label>
            <span>Click timeout ms</span>
            <input
              type="number"
              min={500}
              max={60_000}
              value={form.click_timeout_ms}
              onChange={(event) => setForm({ ...form, click_timeout_ms: Number(event.target.value) })}
            />
          </label>
          <label>
            <span>Debug folder</span>
            <input
              value={form.debug_dump_dir || ""}
              onChange={(event) => setForm({ ...form, debug_dump_dir: event.target.value })}
            />
          </label>
        </div>
        <label className="check-row">
          <input
            type="checkbox"
            checked={form.headed}
            onChange={(event) => setForm({ ...form, headed: event.target.checked })}
          />
          <span>Headed browser</span>
        </label>
      </section>

      <RunPanel runId={runId} title="Scrape run" />
    </div>
  );
}
