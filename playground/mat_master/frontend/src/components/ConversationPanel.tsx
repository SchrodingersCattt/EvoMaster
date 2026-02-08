"use client";

import type { LogEntry } from "./LogStream";
import { renderContent as renderContentValue } from "./ContentRenderer";

function cardClass(source: string): string {
  const base = "border p-3 rounded-lg ";
  switch (source) {
    case "User":
      return base + "border-[#1e40af] bg-[#eff6ff]";
    case "MatMaster":
      return base + "border-[#1e40af] bg-[#eff6ff]";
    case "Planner":
      return base + "border-[#1e3a8a] bg-[#eff6ff]";
    case "Coder":
      return base + "border-[#1e40af] bg-[#eff6ff]";
    case "ToolExecutor":
      return base + "border-amber-600 bg-amber-50/80";
    case "System":
      return base + "border-gray-400 bg-gray-100/80";
    default:
      return base + "border-gray-400 bg-gray-50/80";
  }
}

function renderEntry(entry: LogEntry): React.ReactNode {
  if (entry.type === "planner_reply" && typeof entry.content === "string") {
    return <div className="text-sm">Planner 回复: {entry.content}</div>;
  }
  if (entry.type === "tool_result" && entry.content && typeof entry.content === "object") {
    const c = entry.content as { name?: string; result?: string };
    return (
      <div className="text-xs space-y-1">
        {c.name && <div className="font-medium">{c.name}</div>}
        {renderContentValue(typeof c.result === "string" ? c.result : c, { maxPreHeight: "max-h-40" })}
      </div>
    );
  }
  return renderContentValue(entry.content);
}

function isEnvRelatedToolResult(entry: LogEntry): boolean {
  if (entry.type !== "tool_result" || !entry.content || typeof entry.content !== "object") return false;
  const c = entry.content as { name?: string; result?: string; command?: string; args?: string };
  const s = [c.name, c.result, c.command, typeof c.args === "string" ? c.args : ""].filter(Boolean).join(" ").toLowerCase();
  return s.includes("env");
}

export default function ConversationPanel({
  entries,
  scrollRef,
}: {
  entries: LogEntry[];
  scrollRef?: React.RefObject<HTMLDivElement | null>;
}) {
  const filtered = entries.filter(
    (e) => e.source !== "System" && e.type !== "log_line" && !isEnvRelatedToolResult(e)
  );
  return (
    <div
      ref={scrollRef}
      className="flex flex-col gap-3 overflow-y-auto flex-1 min-h-0 border border-gray-300 rounded-lg p-3 bg-white"
    >
      <h2 className="text-sm font-semibold mb-1 text-[#1e293b] flex-shrink-0">对话</h2>
      {filtered.length === 0 ? (
        <div className="text-xs text-gray-500 flex-1">暂无消息</div>
      ) : (
        filtered.map((log, i) => (
          <div key={i} className={cardClass(log.source)}>
            <div className="text-xs font-bold mb-1 opacity-70">{log.source}</div>
            {renderEntry(log)}
          </div>
        ))
      )}
    </div>
  );
}
