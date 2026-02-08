"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import FileTree from "./FileTree";
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
  const inputRef = useRef("");

  const [sessionIds, setSessionIds] = useState<string[]>(["demo_session"]);
  const [currentSessionId, setCurrentSessionId] = useState("demo_session");
  const currentSessionIdRef = useRef(currentSessionId);
  const runningSessionIdRef = useRef(runningSessionId);
  currentSessionIdRef.current = currentSessionId;
  runningSessionIdRef.current = runningSessionId;
  inputRef.current = input;

  const [mode, setMode] = useState<"direct" | "planner">("direct");
  const [plannerAsk, setPlannerAsk] = useState<string | null>(null);
  const [plannerInput, setPlannerInput] = useState("");
  const [filePath, setFilePath] = useState("");
  const [reconnectTrigger, setReconnectTrigger] = useState(0);
  const [sessionFilesLogsKey, setSessionFilesLogsKey] = useState(0);
  const [liveLogLines, setLiveLogLines] = useState<string[]>([]);

  const isReadOnly = readOnly || (externalLogs !== undefined && externalLogs.length > 0);

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
    let cancelled = false;
    const connect = () => {
      setStatus("connecting");
      const ws = new WebSocket(WS_URL);
      wsRef.current = ws;
      ws.onopen = () => {
        if (!cancelled) setStatus("connected");
      };
      ws.onclose = () => {
        wsRef.current = null;
        if (!cancelled) setStatus("closed");
      };
      ws.onerror = () => {
        wsRef.current = null;
        if (!cancelled) setStatus("closed");
      };
      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data) as LogEntry;
          const sid = msg.session_id;
          const cur = currentSessionIdRef.current;
          setLogs((prev) => {
            if (sid !== undefined && sid !== cur) return prev;
            return [...prev, msg];
          });
          if (msg.type === "planner_ask") {
            setPlannerAsk(typeof msg.content === "string" ? msg.content : "");
            setPlannerInput("");
          } else {
            setPlannerAsk(null);
          }
          if (msg.type === "log_line" && typeof msg.content === "string") {
            if (sid === currentSessionIdRef.current) setLiveLogLines((prev) => [...prev, msg.content as string]);
          }
        if (msg.type === "finish" || msg.type === "error" || msg.type === "cancelled") {
            if (sid === runningSessionIdRef.current) setRunningSessionId(null);
            setRunning(false);
            if (sid === currentSessionIdRef.current) setSessionFilesLogsKey((k) => k + 1);
          }
        } catch {
          // ignore
        }
      };
      return ws;
    };
    const ws = connect();
    return () => {
      cancelled = true;
      if (ws && ws.readyState === WebSocket.OPEN) ws.close();
      wsRef.current = null;
    };
  }, [isReadOnly, externalLogs, reconnectTrigger]);

  // Auto-reconnect when closed (e.g. backend restarted)
  useEffect(() => {
    if (isReadOnly || externalLogs !== undefined || status !== "closed") return;
    const t = setTimeout(() => setReconnectTrigger((k) => k + 1), 2000);
    return () => clearTimeout(t);
  }, [isReadOnly, externalLogs, status]);

  const send = useCallback(() => {
    const content = (inputRef.current || input).trim();
    if (!content) return;
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    const cur = currentSessionIdRef.current;
    const isRunning = running && runningSessionIdRef.current === cur;
    if (isRunning) return;
    setRunning(true);
    setRunningSessionId(cur);
    setLiveLogLines([]);
    setInput("");
    ws.send(JSON.stringify({ content, mode, session_id: cur }));
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
          if (fromApi.length > 0) setSessionIds((prev) => Array.from(new Set([...prev, ...fromApi])));
        })
        .catch(() => {});
    }
  }, [status, isReadOnly, externalLogs]);

  useEffect(() => {
    if (isReadOnly || externalLogs !== undefined) return;
    loadSessionHistory(currentSessionId);
    setLiveLogLines([]);
  }, [currentSessionId, isReadOnly, externalLogs, loadSessionHistory]);

  return (
    <div className="flex flex-col h-full min-h-0 gap-3 p-4 overflow-hidden">
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
            onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); send(); } }}
            placeholder="在此输入任务描述，按 Enter 或点击发送"
            className="flex-1 min-w-[200px] border border-gray-300 rounded px-3 py-2 bg-white text-[#1f2937]"
            disabled={status !== "connected" || (running && currentSessionId === runningSessionId)}
            aria-label="任务描述"
          />
          <button
            type="button"
            onClick={(e) => { e.preventDefault(); send(); }}
            title={
              status !== "connected"
                ? "请等待连接"
                : running && currentSessionId === runningSessionId
                  ? "当前会话运行中"
                  : !input.trim()
                    ? "请输入内容"
                    : "发送"
            }
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
          <span
            className={`text-xs font-medium px-2 py-0.5 rounded ${
              status === "connected"
                ? "bg-green-100 text-green-800"
                : status === "connecting"
                  ? "bg-amber-100 text-amber-800"
                  : "bg-red-100 text-red-800"
            }`}
            title={status === "closed" ? "2 秒后自动重连" : undefined}
          >
            {status === "connecting" && "连接中…"}
            {status === "connected" && "已连接"}
            {status === "closed" && "已断开（重连中）"}
            {status === "idle" && "—"}
          </span>
        </div>
      )}

      <div className="grid grid-cols-[minmax(240px,1fr)_minmax(280px,1.2fr)_minmax(280px,1.2fr)] gap-4 flex-1 min-h-0">
        <div className="flex flex-col gap-3 min-h-0 overflow-hidden">
          <LogPanel
            key={`${currentSessionId}-${sessionFilesLogsKey}`}
            sessionId={isReadOnly ? null : currentSessionId}
            liveLogLines={liveLogLines}
          />
          <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
            <FileTree
              key={`${currentSessionId}-${sessionFilesLogsKey}`}
              sessionId={isReadOnly ? null : currentSessionId}
              filePath={filePath}
              onFilePathChange={setFilePath}
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
