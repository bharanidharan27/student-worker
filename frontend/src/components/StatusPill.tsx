import type { ReactElement } from "react";

import { runStatusTone } from "../utils/runStatus";

interface StatusPillProps {
  value: string | null | undefined;
}

export function StatusPill({ value }: StatusPillProps): ReactElement {
  const text = value || "unknown";
  const tone = runStatusTone(text);

  return <span className={`status-pill status-pill--${tone}`}>{text.replaceAll("_", " ")}</span>;
}
