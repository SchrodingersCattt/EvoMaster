"use client";

import { useCallback, useEffect, useRef, useState } from "react";

const WS_URL =
  typeof window !== "undefined"
    ? (process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000/ws/chat")
    : "";
const API_BASE =
  typeof window !== "undefined"
    ? (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000")
    : "";

export type LogEntry = {
  source: string;
  type: string;
  content: unknown;
};

type RunItem = { id: string; label: string };
type FileEntry = { name: string; path: string; dir: boolean };

function cardClass(source: string): string {
  const base = "border p-3 rounded-lg ";
  switch (source) {
    case "MatMaster":
      return base + "border-[#1e40af] bg-[#eff6ff]";
    case "Planner":
      return base + "border-[#1e3a8a] bg-[#eff6ff]";
    case "Coder":
      return base + "border-[#1e40af] bg-[#eff6ff]";
    case "ToolExecutor":
      return base + "border-[#b91c1c] bg-[#fef2f2]";
    case "System":
      return base + "border-gray-400 bg-gray-100/80";
    default:
      return base + "border-gray-400 bg-gray-50/80";
  }
}

function renderContent(entry: LogEntry): React.ReactNode {
  if (entry.type === "thought" && typeof entry.content === "string") {
    const text = entry.content.trim();
    return (
      <div className="text-sm whitespace-pre-wrap">
        {text || <span className="text-gray-500 italic">(无文本输出)</span>}
      </div>
    );
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
    <pre className="text-xs overflow-x-auto">
      {JSON.stringify(entry.content, null, 2)}
    </pre>
  );
}

export default function LogStream({
  logs: externalLogs,
  readOnly = false,
}: {
  logs?: LogEntry[];
  readOnly?: boolean;
}) {
  const [logs, setLogs] = useState<LogEntry[]>(externalLogs ?? []);
  const [input, setInput] = useState("");
  const [status, setStatus] = useState<"idle" | "connecting" | "connected" | "closed">("idle");
  const [running, setRunning] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const isReadOnly = readOnly || (externalLogs !== undefined && externalLogs.length > 0);

  useEffect(() => {
    if (isReadOnly || externalLogs !== undefined) {
      setLogs(externalLogs ?? []);
      return;
    }
    setStatus("connecting");
    const ws = new WebSocket(WS_URL);
    wsRef.current = ws;

    ws.onopen = () => setStatus("connected");
    ws.onclose = () => {
      setStatus("closed");
      wsRef.current = null;
    };
    ws.onerror = () => setStatus("closed");
    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data) as LogEntry;
        setLogs((prev) => [...prev, msg]);
        if (msg.type === "planner_ask") {
          setPlannerAsk(typeof msg.content === "string" ? msg.content : "");
          setPlannerInput("");
        } else {
          setPlannerAsk(null);
        }
        if (msg.type === "finish" || msg.type === "error" || msg.type === "cancelled") {
          setRunning(false);
        }
      } catch {
        // ignore
      }
    };

    return () => {
      ws.close();
      wsRef.current = null;
    };
  }, [isReadOnly, externalLogs]);

  useEffect(() => {
    if (containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [logs]);

  const send = useCallback(() => {
    const content = input.trim();
    if (!content || !wsRef.current || wsRef.current.readyState !== WebSocket.OPEN || running) return;
    setRunning(true);
    wsRef.current.send(JSON.stringify({ content, mode }));
    setInput("");
  }, [input, running, mode]);

  const cancel = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN && running) {
      wsRef.current.send(JSON.stringify({ type: "cancel" }));
    }
  }, [running]);

  const sendPlannerReply = useCallback(
    (content: string) => {
      if (wsRef.current?.readyState !== WebSocket.OPEN) return;
      wsRef.current.send(JSON.stringify({ type: "planner_reply", content: (content || "").trim() || "abort" }));
      setPlannerAsk(null);
      setPlannerInput("");
    },
    []
  );

  const [mode, setMode] = useState<"direct" | "planner">("direct");
  const [plannerAsk, setPlannerAsk] = useState<string | null>(null);
  const [plannerInput, setPlannerInput] = useState("");
  const [runs, setRuns] = useState<RunItem[]>([{ id: "dev", label: "dev (current)" }]);
  const [selectedRun, setSelectedRun] = useState("dev");
  const [filePath, setFilePath] = useState("");
  const [files, setFiles] = useState<FileEntry[]>([]);

  const loadRuns = useCallback(() => {
    fetch(`${API_BASE}/api/runs`)
      .then((r) => r.json())
      .then((d: { runs: RunItem[] }) => {
        setRuns(d.runs);
        if (d.runs.length && !d.runs.find((r) => r.id === selectedRun)) {
          setSelectedRun(d.runs[0].id);
        }
      })
      .catch(() => {});
  }, [selectedRun]);

  const loadFiles = useCallback(
    (runId: string, path: string) => {
      const url = `${API_BASE}/api/runs/${encodeURIComponent(runId)}/files${path ? `?path=${encodeURIComponent(path)}` : ""}`;
      fetch(url)
        .then((r) => r.json())
        .then((d: { entries: FileEntry[] }) => setFiles(d.entries))
        .catch(() => setFiles([]));
    },
    []
  );

  useEffect(() => {
    if (!isReadOnly && status === "connected") loadRuns();
  }, [isReadOnly, status, loadRuns]);

  useEffect(() => {
    loadFiles(selectedRun, filePath);
  }, [selectedRun, filePath, loadFiles]);

  const openDir = (entry: FileEntry) => {
    if (entry.dir) setFilePath(entry.path || entry.name);
  };

  const goUp = () => {
    const parts = filePath.split(/[/\\]/).filter(Boolean);
    parts.pop();
    setFilePath(parts.join("/"));
  };

  return (
    <div className="flex flex-col h-full max-h-[85vh] gap-3 p-4">
      {!isReadOnly && plannerAsk !== null && (
        <div className="flex-shrink-0 p-3 rounded-lg border border-[#1e40af] bg-[#eff6ff]">
          <div className="text-sm font-medium text-[#1e293b] mb-2">Planner 需确认</div>
          <div className="text-sm mb-2 whitespace-pre-wrap text-[#1f2937]">{plannerAsk}</div>
          <div className="flex gap-2 items-center flex-wrap">
            <input
              type="text"
              value={plannerInput}
              onChange={(e) => setPlannerInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && sendPlannerReply(plannerInput || "go")}
              placeholder="输入 go / abort 或修改意见"
              className="flex-1 min-w-[160px] rounded border border-gray-300 px-2 py-1.5 text-sm bg-white text-[#1f2937]"
            />
            <button
              type="button"
              onClick={() => sendPlannerReply("go")}
              className="px-3 py-1.5 rounded bg-[#1e40af] text-white text-sm"
            >
              Go
            </button>
            <button
              type="button"
              onClick={() => sendPlannerReply("abort")}
              className="px-3 py-1.5 rounded bg-[#b91c1c] text-white text-sm"
            >
              Abort
            </button>
            {plannerInput && (
              <button
                type="button"
                onClick={() => sendPlannerReply(plannerInput)}
                className="px-3 py-1.5 rounded bg-gray-500 text-white text-sm"
              >
                发送修改意见
              </button>
            )}
          </div>
        </div>
      )}
      {!isReadOnly && (
        <div className="flex gap-2 items-center flex-shrink-0 flex-wrap">
          <span className="text-sm text-gray-600">Mode</span>
          <select
            value={mode}
            onChange={(e) => setMode(e.target.value as "direct" | "planner")}
            disabled={running}
            className="rounded border border-gray-300 px-2 py-1.5 text-sm bg-white text-[#1f2937]"
          >
            <option value="direct">Direct</option>
            <option value="planner">Planner</option>
          </select>
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && send()}
            placeholder="输入任务描述..."
            className="flex-1 min-w-[200px] border border-gray-300 rounded px-3 py-2 bg-white text-[#1f2937]"
            disabled={status !== "connected" || running}
          />
          <button
            type="button"
            onClick={send}
            disabled={status !== "connected" || running || !input.trim()}
            className="px-4 py-2 rounded bg-[#1e40af] text-white disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {running ? "运行中..." : "发送"}
          </button>
          <button
            type="button"
            onClick={cancel}
            disabled={!running || status !== "connected"}
            className="px-4 py-2 rounded bg-[#b91c1c] text-white disabled:opacity-50 disabled:cursor-not-allowed"
          >
            终止
          </button>
        </div>
      )}

      {!isReadOnly && (
        <div className="text-xs text-gray-600 flex-shrink-0">
          {status === "connecting" && "连接中..."}
          {status === "connected" && "已连接"}
          {status === "closed" && "连接已断开"}
        </div>
      )}

      <div className="flex gap-4 flex-1 min-h-0">
        <div
          ref={containerRef}
          className="flex flex-col gap-3 overflow-y-auto flex-1 min-w-0"
        >
          {logs.map((log, i) => (
            <div key={i} className={cardClass(log.source)}>
              <div className="text-xs font-bold mb-1 opacity-70">{log.source}</div>
              {renderContent(log)}
            </div>
          ))}
        </div>

        {!isReadOnly && (
          <div className="w-72 flex-shrink-0 border border-gray-300 rounded-lg p-3 bg-[#f3f4f6] flex flex-col">
            <h2 className="text-sm font-semibold mb-2 text-[#1e293b]">Runs / 文件</h2>
            <select
              value={selectedRun}
              onChange={(e) => setSelectedRun(e.target.value)}
              className="w-full rounded border border-gray-300 px-2 py-1 text-sm mb-2 bg-white text-[#1f2937]"
            >
              {runs.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.label}
                </option>
              ))}
            </select>
            <div className="text-xs text-gray-600 mb-1 truncate">
              {selectedRun}{filePath ? ` / ${filePath}` : ""}
            </div>
            <div className="flex-1 overflow-y-auto text-sm">
              {filePath && (
                <div
                  className="py-1 text-[#1e40af] cursor-pointer"
                  onClick={goUp}
                >
                  ..
                </div>
              )}
              {files.map((e) => (
                <div
                  key={e.path || e.name}
                  className={`py-1 ${e.dir ? "text-[#1e40af] cursor-pointer" : "text-[#1f2937]"}`}
                  onClick={() => e.dir && openDir(e)}
                >
                  {e.dir ? `${e.name}/` : e.name}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
