"use client";

import MatMasterView, { type LogEntry } from "./MatMasterView";

export type { LogEntry };

export default function LogStream({
  logs,
  readOnly = false,
}: {
  logs?: LogEntry[];
  readOnly?: boolean;
}) {
  return <MatMasterView logs={logs} readOnly={readOnly} />;
}
