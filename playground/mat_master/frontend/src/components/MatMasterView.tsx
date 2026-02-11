"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { BotIcon, WifiIcon, WifiOffIcon, Loader2Icon } from "./icons";
import { cn } from "@/lib/utils";
import WorkspacePanel from "./WorkspacePanel";
import ChatPanel from "./ChatPanel";

const WS_URL =
  typeof window !== "undefined"
    ? (process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:50001/ws/chat")
    : "";
const API_BASE =
  typeof window !== "undefined"
    ? (process.env.NEXT_PUBLIC_API_URL || "http://localhost:50001")
    : "";

export type LogEntry = {
  msg_id?: number;
  source: string;
  type: string;
  content: unknown;
  session_id?: string;
};

export default function MatMasterView({
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
  const [askHumanQuestion, setAskHumanQuestion] = useState<string | null>(null);
  const [askHumanInput, setAskHumanInput] = useState("");
  const [filePath, setFilePath] = useState("");
  const [reconnectTrigger, setReconnectTrigger] = useState(0);
  const [sessionFilesLogsKey, setSessionFilesLogsKey] = useState(0);
  const [leftWidthPercent, setLeftWidthPercent] = useState(40);
  const [isDragging, setIsDragging] = useState(false);

  const isReadOnly = readOnly || (externalLogs !== undefined && externalLogs.length > 0);

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
            if (msg.type === "log_line" || msg.source === "System") return prev;
            return [...prev, msg];
          });
          if (msg.type === "planner_ask") {
            setPlannerAsk(typeof msg.content === "string" ? msg.content : "");
            setPlannerInput("");
          } else {
            setPlannerAsk(null);
          }
          if (msg.type === "ask_human") {
            let q = "";
            if (typeof msg.content === "string") {
              q = msg.content;
            } else if (msg.content && typeof msg.content === "object") {
              const c = msg.content as { question?: string; context?: string };
              q = c.question || "";
              if (c.context) q += "\n\n" + c.context;
            }
            setAskHumanQuestion(q || "The agent is asking for your input.");
            setAskHumanInput("");
          }
          if (msg.type === "finish" || msg.type === "error" || msg.type === "cancelled") {
            if (sid === runningSessionIdRef.current) setRunningSessionId(null);
            setRunning(false);
            setAskHumanQuestion(null);
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
    setInput("");
    ws.send(JSON.stringify({ content, mode, session_id: cur }));
  }, [input, running, mode, currentSessionId]);

  const cancel = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN && running) {
      wsRef.current.send(
        JSON.stringify({
          type: "cancel",
          session_id: runningSessionId || currentSessionId,
        })
      );
    }
  }, [running, runningSessionId, currentSessionId]);

  const sendPlannerReply = useCallback(
    (content: string) => {
      if (wsRef.current?.readyState !== WebSocket.OPEN) return;
      wsRef.current.send(
        JSON.stringify({
          type: "planner_reply",
          content: (content || "").trim() || "abort",
          session_id: currentSessionId,
        })
      );
      setPlannerAsk(null);
      setPlannerInput("");
    },
    [currentSessionId]
  );

  const sendAskHumanReply = useCallback(
    (content: string) => {
      if (wsRef.current?.readyState !== WebSocket.OPEN) return;
      wsRef.current.send(
        JSON.stringify({
          type: "ask_human_reply",
          content: (content || "").trim(),
          session_id: currentSessionId,
        })
      );
      setAskHumanQuestion(null);
      setAskHumanInput("");
    },
    [currentSessionId]
  );

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
          if (fromApi.length > 0)
            setSessionIds((prev) => Array.from(new Set([...prev, ...fromApi])));
        })
        .catch(() => {});
    }
  }, [status, isReadOnly, externalLogs]);

  useEffect(() => {
    if (isReadOnly || externalLogs !== undefined) return;
    loadSessionHistory(currentSessionId);
  }, [currentSessionId, isReadOnly, externalLogs, loadSessionHistory]);

  const handleResizeMove = useCallback(
    (e: MouseEvent) => {
      if (!isDragging) return;
      const total = window.innerWidth;
      const x = e.clientX;
      const pct = Math.min(60, Math.max(20, (x / total) * 100));
      setLeftWidthPercent(pct);
    },
    [isDragging]
  );
  const handleResizeEnd = useCallback(() => setIsDragging(false), []);
  useEffect(() => {
    if (!isDragging) return;
    window.addEventListener("mousemove", handleResizeMove);
    window.addEventListener("mouseup", handleResizeEnd);
    return () => {
      window.removeEventListener("mousemove", handleResizeMove);
      window.removeEventListener("mouseup", handleResizeEnd);
    };
  }, [isDragging, handleResizeMove, handleResizeEnd]);

  return (
    <main className="h-screen flex flex-col overflow-hidden bg-background text-foreground">
      <header className="flex-shrink-0 h-12 px-4 flex items-center justify-between border-b border-zinc-200 dark:border-zinc-800 bg-card">
        <div className="flex items-center gap-3">
          <BotIcon size={20} className="text-zinc-600 dark:text-zinc-400 shrink-0" />
          <h1 className="text-sm font-semibold text-zinc-800 dark:text-zinc-200">
            EvoMaster-Mat
          </h1>
        </div>
        {!isReadOnly && (
          <div
            className={cn(
              "flex items-center gap-2 text-xs font-medium px-2 py-1 rounded-md",
              status === "connected"
                ? "bg-emerald-50 dark:bg-emerald-950/40 text-emerald-700 dark:text-emerald-400"
                : status === "connecting"
                  ? "bg-amber-50 dark:bg-amber-950/40 text-amber-700 dark:text-amber-400"
                  : "bg-red-50 dark:bg-red-950/40 text-red-700 dark:text-red-400"
            )}
          >
            {status === "connecting" && <Loader2Icon size={14} className="shrink-0" />}
            {status === "connected" && <WifiIcon size={14} className="shrink-0" />}
            {(status === "closed" || status === "idle") && <WifiOffIcon size={14} className="shrink-0" />}
            {status === "connecting" && "Connecting…"}
            {status === "connected" && "Connected"}
            {status === "closed" && "Disconnected"}
            {status === "idle" && "—"}
          </div>
        )}
      </header>

      <div className="flex-1 min-h-0 flex overflow-hidden">
        <div
          className="flex flex-col shrink-0 min-h-0 overflow-hidden border-r border-zinc-200 dark:border-zinc-800"
          style={{ width: `${leftWidthPercent}%` }}
        >
          <WorkspacePanel
            entries={logs}
            sessionId={isReadOnly ? null : currentSessionId}
            filePath={filePath}
            onFilePathChange={setFilePath}
            sessionFilesLogsKey={sessionFilesLogsKey}
            readOnly={isReadOnly}
          />
        </div>
        <div
          role="separator"
          aria-label="Resize panels"
          onMouseDown={() => setIsDragging(true)}
          className={cn(
            "w-1 shrink-0 cursor-col-resize bg-zinc-200 dark:bg-zinc-800 hover:bg-zinc-300 dark:hover:bg-zinc-700 transition-colors",
            isDragging && "bg-zinc-400 dark:bg-zinc-600"
          )}
        />
        <div className="flex-1 min-w-0 min-h-0 overflow-hidden">
          <ChatPanel
            entries={logs}
            scrollRef={conversationRef}
            input={input}
            setInput={setInput}
            onSend={send}
            onCancel={cancel}
            status={status}
            running={running}
            currentSessionId={currentSessionId}
            runningSessionId={runningSessionId}
            sessionIds={sessionIds}
            setCurrentSessionId={setCurrentSessionId}
            addNewSession={addNewSession}
            mode={mode}
            setMode={setMode}
            plannerAsk={plannerAsk}
            plannerInput={plannerInput}
            setPlannerInput={setPlannerInput}
            sendPlannerReply={sendPlannerReply}
            askHumanQuestion={askHumanQuestion}
            askHumanInput={askHumanInput}
            setAskHumanInput={setAskHumanInput}
            sendAskHumanReply={sendAskHumanReply}
            readOnly={isReadOnly}
          />
        </div>
      </div>
    </main>
  );
}
