"use client";

import type { LogEntry } from "./LogStream";

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

function renderContent(entry: LogEntry): React.ReactNode {
  if (entry.type === "thought" && typeof entry.content === "string") {
    const text = entry.content.trim();
    if (!text) return <div className="text-sm text-gray-500 italic">(无文本)</div>;
    return <div className="text-sm whitespace-pre-wrap">{text}</div>;
  }
  if (entry.type === "tool_call" && entry.content && typeof entry.content === "object") {
    return (
      <pre className="text-xs bg-gray-200 p-2 rounded overflow-x-auto max-h-40 overflow-y-auto text-[#1f2937]">
        {JSON.stringify(entry.content, null, 2)}
      </pre>
    );
  }
  if (entry.type === "tool_result" && entry.content && typeof entry.content === "object") {
    const c = entry.content as { name?: string; result?: string };
    return (
      <div className="text-xs space-y-1">
        {c.name && <div className="font-medium">{c.name}</div>}
        <pre className="bg-gray-200 p-2 rounded overflow-x-auto max-h-40 overflow-y-auto text-[#1f2937]">
          {typeof c.result === "string" ? c.result : JSON.stringify(c)}
        </pre>
      </div>
    );
  }
  if (typeof entry.content === "string") {
    return <div className="text-sm">{entry.content}</div>;
  }
  return <pre className="text-xs overflow-x-auto">{JSON.stringify(entry.content, null, 2)}</pre>;
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
              {renderContent(log)}
            </div>
          ))
        )}
      </div>
    </div>
  );
}
