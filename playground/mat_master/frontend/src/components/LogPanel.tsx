"use client";

import { useCallback, useEffect, useRef, useState } from "react";

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
  sessionId,
  liveLogLines = [],
}: {
  sessionId: string | null;
  liveLogLines?: string[];
}) {
  const [content, setContent] = useState("");
  const [activeTab, setActiveTab] = useState<"all" | "INFO" | "ERROR">("all");
  const [loading, setLoading] = useState(false);
  const [logList, setLogList] = useState<{ task_id: string; path: string }[]>([]);
  const [taskId, setTaskId] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!sessionId) {
      setLogList([]);
      setTaskId(null);
      setContent("");
      return;
    }
    fetch(`${API_BASE}/api/sessions/${encodeURIComponent(sessionId)}/logs`)
      .then((r) => (r.ok ? r.json() : { logs: [] }))
      .then((d: { logs: { task_id: string; path: string }[] }) => {
        setLogList(d.logs || []);
        setTaskId(d.logs?.length ? d.logs[0].task_id ?? null : null);
      })
      .catch(() => setLogList([]));
  }, [sessionId]);

  const loadLog = useCallback((sid: string, tid: string) => {
    setLoading(true);
    const url = `${API_BASE}/api/sessions/${encodeURIComponent(sid)}/logs?task_id=${encodeURIComponent(tid)}`;
    fetch(url)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error("Not found"))))
      .then((d: { content?: string }) => setContent(d.content ?? ""))
      .catch(() => setContent(""))
      .finally(() => setLoading(false));
  }, []);

  const isCurrentTask = logList.length > 0 && taskId === logList[0].task_id;
  useEffect(() => {
    if (!sessionId || !taskId || isCurrentTask) {
      if (!isCurrentTask) setContent("");
      return;
    }
    loadLog(sessionId, taskId);
  }, [sessionId, taskId, isCurrentTask, loadLog]);

  const displayContent = isCurrentTask ? liveLogLines.join("\n") : content;
  const parsed = parseLogContent(displayContent);
  useEffect(() => {
    if (isCurrentTask && scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [isCurrentTask, liveLogLines.length]);
  const infoLines = parsed.filter((l) => l.level === "INFO");
  const errorLines = parsed.filter((l) => l.level === "ERROR");
  const displayLines =
    activeTab === "INFO" ? infoLines : activeTab === "ERROR" ? errorLines : parsed;

  return (
    <div className="border border-gray-300 rounded-lg p-3 bg-[#f3f4f6] flex flex-col min-h-[200px] flex-1 min-h-0">
      <h2 className="text-sm font-semibold mb-2 text-[#1e293b] flex-shrink-0">
        控制台 {isCurrentTask && liveLogLines.length > 0 ? "(实时)" : ""}
      </h2>
      {logList.length > 0 && (
        <select
          className="w-full rounded border border-gray-300 px-2 py-1 text-xs mb-2 bg-white text-[#1f2937] flex-shrink-0"
          value={taskId || logList[0]?.task_id || ""}
          onChange={(e) => {
            const tid = e.target.value;
            if (tid && sessionId) {
              setTaskId(tid);
              loadLog(sessionId, tid);
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
      <div
        ref={scrollRef}
        className="flex-1 min-h-0 overflow-y-auto overflow-x-auto font-mono text-xs bg-[#1f2937] text-gray-200 p-2 rounded whitespace-pre-wrap break-all"
      >
        {loading && !isCurrentTask ? (
          <div className="text-gray-500">加载中...</div>
        ) : displayLines.length === 0 ? (
          <div className="text-gray-500">
            {displayContent ? "无匹配行" : "当前会话暂无日志"}
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
