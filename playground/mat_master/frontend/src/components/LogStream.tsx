"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import FileTree, { type FileEntry, type RunItem } from "./FileTree";
import LogPanel from "./LogPanel";
import StatusPanel from "./StatusPanel";
import ConversationPanel from "./ConversationPanel";

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
  session_id?: string;
};

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
  const [runningSessionId, setRunningSessionId] = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const conversationRef = useRef<HTMLDivElement>(null);

  const [sessionIds, setSessionIds] = useState<string[]>(["demo_session"]);
  const [currentSessionId, setCurrentSessionId] = useState("demo_session");
  const [mode, setMode] = useState<"direct" | "planner">("direct");
  const [plannerAsk, setPlannerAsk] = useState<string | null>(null);
  const [plannerInput, setPlannerInput] = useState("");
  const [runs, setRuns] = useState<RunItem[]>([{ id: "mat_master_web", label: "mat_master_web" }]);
  const [selectedRun, setSelectedRun] = useState("mat_master_web");
  const [filePath, setFilePath] = useState("");
  const [files, setFiles] = useState<FileEntry[]>([]);
  const [logList, setLogList] = useState<{ task_id: string; path: string }[]>([]);
  const [selectedLogTaskId, setSelectedLogTaskId] = useState<string | null>(null);

  const isReadOnly = readOnly || (externalLogs !== undefined && externalLogs.length > 0);

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

  const loadFiles = useCallback((runId: string, path: string) => {
    const url = `${API_BASE}/api/runs/${encodeURIComponent(runId)}/files${path ? `?path=${encodeURIComponent(path)}` : ""}`;
    fetch(url)
      .then((r) => r.json())
      .then((d: { entries: FileEntry[] }) => setFiles(d.entries))
      .catch(() => setFiles([]));
  }, []);

  useEffect(() => {
    if (!isReadOnly && status === "connected") loadRuns();
  }, [isReadOnly, status, loadRuns]);

  const loadLogList = useCallback((runId: string) => {
    fetch(`${API_BASE}/api/runs/${encodeURIComponent(runId)}/logs`)
      .then((r) => (r.ok ? r.json() : { logs: [] }))
      .then((d: { logs: { task_id: string; path: string }[] }) => {
        setLogList(d.logs || []);
        setSelectedLogTaskId(d.logs?.length ? d.logs[0].task_id ?? null : null);
      })
      .catch(() => setLogList([]));
  }, []);

  useEffect(() => {
    if (selectedRun) loadLogList(selectedRun);
  }, [selectedRun, loadLogList]);

  useEffect(() => {
    if (conversationRef.current) {
      conversationRef.current.scrollTop = conversationRef.current.scrollHeight;
    }
  }, [logs]);

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
        const sid = msg.session_id;
        setLogs((prev) => {
          if (sid !== undefined && sid !== currentSessionId) return prev;
          return [...prev, msg];
        });
        if (msg.type === "planner_ask") {
          setPlannerAsk(typeof msg.content === "string" ? msg.content : "");
          setPlannerInput("");
        } else {
          setPlannerAsk(null);
        }
        if (msg.type === "finish" || msg.type === "error" || msg.type === "cancelled") {
          if (sid === runningSessionId) setRunningSessionId(null);
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
  }, [isReadOnly, externalLogs, currentSessionId, runningSessionId]);

  const send = useCallback(() => {
    const content = input.trim();
    if (!content || !wsRef.current || wsRef.current.readyState !== WebSocket.OPEN || running) return;
    setRunning(true);
    setRunningSessionId(currentSessionId);
    wsRef.current.send(JSON.stringify({ content, mode, session_id: currentSessionId }));
    setInput("");
  }, [input, running, mode, currentSessionId]);

  const cancel = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN && running) {
      wsRef.current.send(JSON.stringify({ type: "cancel", session_id: runningSessionId || currentSessionId }));
    }
  }, [running, runningSessionId, currentSessionId]);

  const sendPlannerReply = useCallback((content: string) => {
    if (wsRef.current?.readyState !== WebSocket.OPEN) return;
    wsRef.current.send(JSON.stringify({ type: "planner_reply", content: (content || "").trim() || "abort", session_id: currentSessionId }));
    setPlannerAsk(null);
    setPlannerInput("");
  }, [currentSessionId]);

  const loadSessionHistory = useCallback((sid: string) => {
    fetch(`${API_BASE}/api/sessions/${encodeURIComponent(sid)}/history`)
      .then((r) => (r.ok ? r.json() : []))
      .then((h: LogEntry[]) => setLogs(h))
      .catch(() => setLogs([]));
  }, []);

  const addNewSession = useCallback(() => {
    const newId = "s_" + Math.random().toString(36).slice(2, 10);
    setSessionIds((prev) => [...prev, newId]);
    setCurrentSessionId(newId);
    setLogs([]);
  }, []);

  useEffect(() => {
    if (isReadOnly || externalLogs !== undefined) return;
    if (status === "connected") {
      fetch(`${API_BASE}/api/sessions`)
        .then((r) => (r.ok ? r.json() : { sessions: [] }))
        .then((d: { sessions: { id: string }[] }) => {
          const fromApi = (d.sessions || []).map((s) => s.id);
          if (fromApi.length > 0) setSessionIds((prev) => [...new Set([...prev, ...fromApi])]);
        })
        .catch(() => {});
    }
  }, [status, isReadOnly, externalLogs]);

  useEffect(() => {
    if (isReadOnly || externalLogs !== undefined) return;
    loadSessionHistory(currentSessionId);
  }, [currentSessionId, isReadOnly, externalLogs, loadSessionHistory]);

  return (
    <div className="flex flex-col h-full min-h-[85vh] gap-3 p-4">
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
          <span className="text-sm text-gray-600">Session</span>
          <select
            value={currentSessionId}
            onChange={(e) => setCurrentSessionId(e.target.value)}
            className="rounded border border-gray-300 px-2 py-1.5 text-sm bg-white text-[#1f2937] min-w-[120px]"
          >
            {sessionIds.map((id) => (
              <option key={id} value={id}>
                {id}
                {id === runningSessionId ? " (运行中)" : ""}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={addNewSession}
            className="px-2 py-1.5 rounded border border-gray-300 text-sm bg-white text-[#1f2937]"
          >
            新建
          </button>
          <span className="text-sm text-gray-600">Mode</span>
          <select
            value={mode}
            onChange={(e) => setMode(e.target.value as "direct" | "planner")}
            disabled={running && currentSessionId === runningSessionId}
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
            disabled={status !== "connected" || (running && currentSessionId === runningSessionId)}
          />
          <button
            type="button"
            onClick={send}
            disabled={status !== "connected" || (running && currentSessionId === runningSessionId) || !input.trim()}
            className="px-4 py-2 rounded bg-[#1e40af] text-white disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {running ? "运行中..." : "发送"}
          </button>
          <button
            type="button"
            onClick={cancel}
            disabled={!running || status !== "connected" || currentSessionId !== runningSessionId}
            className="px-4 py-2 rounded bg-[#b91c1c] text-white disabled:opacity-50 disabled:cursor-not-allowed"
          >
            终止
          </button>
          <span className="text-xs text-gray-600">
            {status === "connecting" && "连接中..."}
            {status === "connected" && "已连接"}
            {status === "closed" && "连接已断开"}
          </span>
        </div>
      )}

      <div className="grid grid-cols-[minmax(240px,1fr)_minmax(280px,1.2fr)_minmax(280px,1.2fr)] gap-4 flex-1 min-h-0">
        <div className="flex flex-col gap-3 min-h-0">
          <LogPanel
            runId={selectedRun}
            taskId={selectedLogTaskId}
            logList={logList}
            onTaskIdChange={setSelectedLogTaskId}
          />
          <div className="flex-1 min-h-[200px]">
            <FileTree
              selectedRunId={selectedRun}
              onRunIdChange={setSelectedRun}
              runIds={runs}
              onLoadRuns={loadRuns}
              filePath={filePath}
              onFilePathChange={setFilePath}
              entries={files}
              onLoadEntries={loadFiles}
            />
          </div>
        </div>

        <div className="flex flex-col min-h-0">
          <StatusPanel entries={logs} />
        </div>

        <div className="flex flex-col min-h-0">
          <ConversationPanel entries={logs} scrollRef={conversationRef} />
        </div>
      </div>
    </div>
  );
}
