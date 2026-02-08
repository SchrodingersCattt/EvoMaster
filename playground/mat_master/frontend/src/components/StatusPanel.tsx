"use client";

import type { LogEntry } from "./LogStream";
import { renderContent } from "./ContentRenderer";

function statusCardClass(source: string): string {
  const base = "border p-3 rounded-lg ";
  switch (source) {
    case "MatMaster":
      return base + "border-[#1e40af] bg-[#eff6ff]";
    case "ToolExecutor":
      return base + "border-amber-600 bg-amber-50/80";
    default:
      return base + "border-gray-400 bg-gray-50/80";
  }
}

function renderEntry(entry: LogEntry): React.ReactNode {
  if (entry.type === "tool_result" && entry.content && typeof entry.content === "object") {
    const c = entry.content as { name?: string; result?: string };
    return (
      <div className="text-xs space-y-1">
        {c.name && <div className="font-medium">{c.name}</div>}
        {renderContent(typeof c.result === "string" ? c.result : c, { maxPreHeight: "max-h-40" })}
      </div>
    );
  }
  return renderContent(entry.content, { maxPreHeight: "max-h-40" });
}

export default function StatusPanel({ entries }: { entries: LogEntry[] }) {
  const filtered = entries.filter((e) => e.source === "MatMaster" || e.source === "ToolExecutor");
  return (
    <div className="border border-gray-300 rounded-lg p-3 bg-[#f9fafb] flex flex-col h-full min-h-0">
      <h2 className="text-sm font-semibold mb-2 text-[#1e293b]">MatMaster / ToolExecutor</h2>
      <div className="flex flex-col gap-2 overflow-y-auto flex-1 min-h-0">
        {filtered.length === 0 ? (
          <div className="text-xs text-gray-500">暂无状态</div>
        ) : (
          filtered.map((log, i) => (
            <div key={i} className={statusCardClass(log.source)}>
              <div className="text-xs font-bold mb-1 opacity-70">{log.source}</div>
              {renderEntry(log)}
            </div>
          ))
        )}
      </div>
    </div>
  );
}
