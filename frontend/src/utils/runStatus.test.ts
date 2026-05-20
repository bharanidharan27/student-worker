import { describe, expect, it } from "vitest";

import { isActiveRunStatus, runStatusTone } from "./runStatus";

describe("runStatus", () => {
  it("detects active run statuses", () => {
    expect(isActiveRunStatus("queued")).toBe(true);
    expect(isActiveRunStatus("running")).toBe(true);
    expect(isActiveRunStatus("waiting_for_user")).toBe(true);
    expect(isActiveRunStatus("completed")).toBe(false);
  });

  it("maps statuses to tones", () => {
    expect(runStatusTone("completed")).toBe("good");
    expect(runStatusTone("waiting_for_user")).toBe("warn");
    expect(runStatusTone("failed")).toBe("bad");
    expect(runStatusTone(undefined)).toBe("neutral");
  });
});
