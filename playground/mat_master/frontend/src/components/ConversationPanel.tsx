"use client";

import type { LogEntry } from "./LogStream";

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

function renderContent(entry: LogEntry): React.ReactNode {
  if (entry.type === "thought" && typeof entry.content === "string") {
    const text = entry.content.trim();
    const isLongOrJson = text.length > 400 || /^\s*[\{\[]/.test(text);
    if (!text) return <div className="text-sm text-gray-500 italic">(无文本输出)</div>;
    if (isLongOrJson) {
      return (
        <pre className="text-xs whitespace-pre-wrap bg-gray-100 p-2 rounded overflow-x-auto max-h-60 overflow-y-auto text-[#1f2937]">
          {text}
        </pre>
      );
    }
    return <div className="text-sm whitespace-pre-wrap">{text}</div>;
  }
  if (entry.type === "query" || (entry.source === "User" && typeof entry.content === "string")) {
    return <div className="text-sm whitespace-pre-wrap">{String(entry.content)}</div>;
  }
  if (entry.type === "planner_reply" && typeof entry.content === "string") {
    return <div className="text-sm">Planner 回复: {entry.content}</div>;
  }
  if (entry.type === "tool_call" && entry.content && typeof entry.content === "object") {
    return (
      <pre className="text-xs bg-gray-200 p-2 rounded overflow-x-auto text-[#1f2937]">
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
  return (
    <pre className="text-xs overflow-x-auto">{JSON.stringify(entry.content, null, 2)}</pre>
  );
}

export default function ConversationPanel({
  entries,
  scrollRef,
}: {
  entries: LogEntry[];
  scrollRef?: React.RefObject<HTMLDivElement | null>;
}) {
  return (
    <div
      ref={scrollRef}
      className="flex flex-col gap-3 overflow-y-auto flex-1 min-h-0 border border-gray-300 rounded-lg p-3 bg-white"
    >
      <h2 className="text-sm font-semibold mb-1 text-[#1e293b] flex-shrink-0">对话</h2>
      {entries.length === 0 ? (
        <div className="text-xs text-gray-500 flex-1">暂无消息</div>
      ) : (
        entries.map((log, i) => (
          <div key={i} className={cardClass(log.source)}>
            <div className="text-xs font-bold mb-1 opacity-70">{log.source}</div>
            {renderContent(log)}
          </div>
        ))
      )}
    </div>
  );
}
