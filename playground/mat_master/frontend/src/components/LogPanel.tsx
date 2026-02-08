"use client";

import { useCallback, useEffect, useState } from "react";

const API_BASE =
  typeof window !== "undefined"
    ? (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000")
    : "";

type LogLine = { level: "INFO" | "ERROR" | "OTHER"; raw: string };

function parseLogContent(content: string): LogLine[] {
  const lines = content.split(/\r?\n/);
  return lines.map((raw) => {
    const m = raw.match(/^(INFO|ERROR|WARNING|DEBUG)\s*[:\-]/);
    if (m) {
      const level = m[1] === "ERROR" ? "ERROR" : m[1] === "INFO" ? "INFO" : "OTHER";
      return { level, raw };
    }
    return { level: "OTHER" as const, raw };
  });
}

export default function LogPanel({
  runId,
  taskId,
  logList,
  onTaskIdChange,
}: {
  runId: string;
  taskId: string | null;
  logList: { task_id: string; path: string }[];
  onTaskIdChange?: (taskId: string) => void;
}) {
  const [content, setContent] = useState("");
  const [activeTab, setActiveTab] = useState<"all" | "INFO" | "ERROR">("all");
  const [loading, setLoading] = useState(false);

  const loadLog = useCallback((rid: string, tid: string) => {
    setLoading(true);
    const url = `${API_BASE}/api/runs/${encodeURIComponent(rid)}/logs?task_id=${encodeURIComponent(tid)}`;
    fetch(url)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error("Not found"))))
      .then((d: { content?: string }) => setContent(d.content ?? ""))
      .catch(() => setContent(""))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!runId || !taskId) {
      setContent("");
      return;
    }
    loadLog(runId, taskId);
  }, [runId, taskId, loadLog]);

  const parsed = parseLogContent(content);
  const infoLines = parsed.filter((l) => l.level === "INFO");
  const errorLines = parsed.filter((l) => l.level === "ERROR");
  const displayLines =
    activeTab === "INFO" ? infoLines : activeTab === "ERROR" ? errorLines : parsed;

  return (
    <div className="border border-gray-300 rounded-lg p-3 bg-[#f3f4f6] flex flex-col min-h-[200px] flex-1 min-h-0">
      <h2 className="text-sm font-semibold mb-2 text-[#1e293b] flex-shrink-0">日志</h2>
      {logList.length > 0 && (
        <select
          className="w-full rounded border border-gray-300 px-2 py-1 text-xs mb-2 bg-white text-[#1f2937] flex-shrink-0"
          value={taskId || logList[0]?.task_id || ""}
          onChange={(e) => {
            const tid = e.target.value;
            if (tid) {
              onTaskIdChange?.(tid);
              loadLog(runId, tid);
            }
          }}
        >
          {logList.map((l) => (
            <option key={l.task_id} value={l.task_id}>
              {l.task_id}
            </option>
          ))}
        </select>
      )}
      <div className="flex gap-1 mb-2 flex-shrink-0">
        <button
          type="button"
          onClick={() => setActiveTab("all")}
          className={`px-2 py-1 text-xs rounded ${activeTab === "all" ? "bg-[#1e40af] text-white" : "bg-gray-200 text-gray-700"}`}
        >
          全部
        </button>
        <button
          type="button"
          onClick={() => setActiveTab("INFO")}
          className={`px-2 py-1 text-xs rounded ${activeTab === "INFO" ? "bg-[#1e40af] text-white" : "bg-gray-200 text-gray-700"}`}
        >
          INFO ({infoLines.length})
        </button>
        <button
          type="button"
          onClick={() => setActiveTab("ERROR")}
          className={`px-2 py-1 text-xs rounded ${activeTab === "ERROR" ? "bg-amber-600 text-white" : "bg-gray-200 text-gray-700"}`}
        >
          ERROR ({errorLines.length})
        </button>
      </div>
      <div className="flex-1 overflow-y-auto min-h-0 font-mono text-xs bg-[#1f2937] text-gray-200 p-2 rounded">
        {loading ? (
          <div className="text-gray-500">加载中...</div>
        ) : displayLines.length === 0 ? (
          <div className="text-gray-500">
            {content ? "无匹配行" : "选择 Run 和 task 后加载"}
          </div>
        ) : (
          displayLines.map((line, i) => (
            <div
              key={i}
              className={
                line.level === "ERROR"
                  ? "text-red-300 whitespace-pre-wrap break-all"
                  : line.level === "INFO"
                    ? "text-sky-200 whitespace-pre-wrap break-all"
                    : "text-gray-400 whitespace-pre-wrap break-all"
              }
            >
              {line.raw}
            </div>
          ))
        )}
      </div>
    </div>
  );
}
