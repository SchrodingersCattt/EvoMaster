"use client";

import { useRef, useEffect } from "react";
import { SendIcon, SquareIcon, Loader2Icon } from "./icons";
import { cn } from "@/lib/utils";
import type { LogEntry } from "./LogStream";
import { renderContent } from "./ContentRenderer";
import { isEnvRelatedEntry } from "@/lib/logEntryUtils";

function renderEntry(entry: LogEntry): React.ReactNode {
  if (entry.type === "planner_reply" && typeof entry.content === "string") {
    return <div className="text-sm">Planner: {entry.content}</div>;
  }
  if (entry.type === "tool_result" && entry.content && typeof entry.content === "object") {
    const c = entry.content as { name?: string; result?: string };
    return (
      <div className="text-xs space-y-1">
        {c.name && <div className="font-medium text-zinc-500 dark:text-zinc-400">{c.name}</div>}
        {renderContent(typeof c.result === "string" ? c.result : c)}
      </div>
    );
  }
  return renderContent(entry.content);
}

function MessageBubble({
  entry,
  isUser,
}: {
  entry: LogEntry;
  isUser: boolean;
}) {
  const content = renderEntry(entry);
  const source = entry.source;
  const hasThoughts =
    source !== "User" &&
    typeof entry.content === "string" &&
    (entry.content.includes("Thought:") || entry.content.includes("Thoughts:"));

  let mainContent: React.ReactNode = content;
  let thoughtsContent: string | null = null;
  if (hasThoughts && typeof entry.content === "string") {
    const thoughtMatch = entry.content.match(
      /(?:Thought|Thoughts):\s*([\s\S]*?)(?=\n\n(?:Final|Answer|$)|$)/i
    );
    if (thoughtMatch) {
      thoughtsContent = thoughtMatch[1].trim();
      const afterThought = entry.content
        .replace(/(?:Thought|Thoughts):\s*[\s\S]*?(?=\n\n|$)/i, "")
        .trim();
      if (afterThought) {
        mainContent = renderContent(afterThought);
      }
    }
  }

  return (
    <div
      className={cn(
        "flex w-full",
        isUser ? "justify-end" : "justify-start"
      )}
    >
      <div
        className={cn(
          "max-w-[85%] rounded-lg px-3 py-2 shadow-sm border",
          isUser
            ? "bg-zinc-100 dark:bg-zinc-800 border-zinc-200 dark:border-zinc-700"
            : "bg-card border-zinc-200 dark:border-zinc-800"
        )}
      >
        {!isUser && (
          <div className="text-xs font-medium text-zinc-500 dark:text-zinc-400 mb-1">{source}</div>
        )}
        {thoughtsContent && (
          <details className="mb-2 group">
            <summary className="text-xs cursor-pointer text-zinc-500 dark:text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-300 list-none flex items-center gap-1">
              <span className="inline-block transition group-open:rotate-90">›</span>
              Thoughts / planning
            </summary>
            <div className="mt-1 pl-3 border-l-2 border-zinc-200 dark:border-zinc-700 text-xs text-zinc-600 dark:text-zinc-400 whitespace-pre-wrap">
              {thoughtsContent}
            </div>
          </details>
        )}
        <div className="text-sm text-zinc-800 dark:text-zinc-200 break-words">{mainContent}</div>
      </div>
    </div>
  );
}

export default function ChatPanel({
  entries,
  scrollRef,
  input,
  setInput,
  onSend,
  onCancel,
  status,
  running,
  currentSessionId,
  runningSessionId,
  sessionIds,
  setCurrentSessionId,
  addNewSession,
  mode,
  setMode,
  plannerAsk,
  plannerInput,
  setPlannerInput,
  sendPlannerReply,
  readOnly = false,
}: {
  entries: LogEntry[];
  scrollRef?: React.RefObject<HTMLDivElement | null>;
  input: string;
  setInput: (v: string) => void;
  onSend: () => void;
  onCancel: () => void;
  status: "idle" | "connecting" | "connected" | "closed";
  running: boolean;
  currentSessionId: string;
  runningSessionId: string | null;
  sessionIds: string[];
  setCurrentSessionId: (id: string) => void;
  addNewSession: () => void;
  mode: "direct" | "planner";
  setMode: (m: "direct" | "planner") => void;
  plannerAsk: string | null;
  plannerInput: string;
  setPlannerInput: (v: string) => void;
  sendPlannerReply: (content: string) => void;
  readOnly?: boolean;
}) {
  const filtered = entries.filter(
    (e) =>
      e.source !== "System" &&
      e.type !== "log_line" &&
      !isEnvRelatedEntry(e)
  );
  const isRunning = running && currentSessionId === runningSessionId;
  const canSend = status === "connected" && !isRunning;

  useEffect(() => {
    if (scrollRef?.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [filtered.length, scrollRef]);

  return (
    <div className="flex flex-col h-full min-h-0 bg-background">
      <div className="flex-1 min-h-0 flex flex-col overflow-hidden">
        <div
          ref={scrollRef}
          className="flex-1 overflow-y-auto px-4 py-4 space-y-4"
        >
          {filtered.length === 0 ? (
            <div className="flex items-center justify-center h-full text-sm text-zinc-500 dark:text-zinc-400">
              No messages yet. Send a task to start.
            </div>
          ) : (
            filtered.map((log, i) => (
              <MessageBubble
                key={i}
                entry={log}
                isUser={log.source === "User"}
              />
            ))
          )}
        </div>

        {!readOnly && plannerAsk !== null && (
          <div className="flex-shrink-0 mx-4 mb-2 p-3 rounded-lg border border-zinc-200 dark:border-zinc-700 bg-zinc-50 dark:bg-zinc-900/50">
            <div className="text-xs font-medium text-zinc-600 dark:text-zinc-400 mb-2">
              Planner confirmation
            </div>
            <div className="text-sm text-zinc-700 dark:text-zinc-300 mb-2 whitespace-pre-wrap">
              {plannerAsk}
            </div>
            <div className="flex gap-2 items-center flex-wrap">
              <input
                type="text"
                value={plannerInput}
                onChange={(e) => setPlannerInput(e.target.value)}
                onKeyDown={(e) =>
                  e.key === "Enter" && sendPlannerReply(plannerInput || "go")
                }
                placeholder="go / abort or feedback"
                className="flex-1 min-w-[140px] rounded-md border border-zinc-300 dark:border-zinc-600 px-2.5 py-1.5 text-sm bg-card text-foreground focus:outline-none focus:ring-2 focus:ring-zinc-400 dark:focus:ring-zinc-500"
              />
              <button
                type="button"
                onClick={() => sendPlannerReply("go")}
                className="px-3 py-1.5 rounded-md bg-zinc-800 dark:bg-zinc-200 text-zinc-100 dark:text-zinc-900 text-sm font-medium hover:opacity-90"
              >
                Go
              </button>
              <button
                type="button"
                onClick={() => sendPlannerReply("abort")}
                className="px-3 py-1.5 rounded-md border border-red-500 text-red-600 dark:text-red-400 text-sm hover:bg-red-50 dark:hover:bg-red-950/30"
              >
                Abort
              </button>
              {plannerInput && (
                <button
                  type="button"
                  onClick={() => sendPlannerReply(plannerInput)}
                  className="px-3 py-1.5 rounded-md border border-zinc-300 dark:border-zinc-600 text-sm text-zinc-700 dark:text-zinc-300 hover:bg-zinc-100 dark:hover:bg-zinc-800"
                >
                  Send feedback
                </button>
              )}
            </div>
          </div>
        )}

        {!readOnly && (
          <div className="flex-shrink-0 p-4">
            <div className="flex gap-2 items-end flex-wrap rounded-xl border border-zinc-200 dark:border-zinc-700 bg-card shadow-sm p-2">
              <div className="flex gap-2 flex-1 min-w-0 flex-wrap items-center">
                <span className="text-xs text-zinc-500 dark:text-zinc-400 shrink-0">Session</span>
                <select
                  value={currentSessionId}
                  onChange={(e) => setCurrentSessionId(e.target.value)}
                  className="rounded-md border border-zinc-300 dark:border-zinc-600 px-2 py-1.5 text-sm bg-background text-foreground min-w-[100px] shrink-0 focus:outline-none focus:ring-2 focus:ring-zinc-400"
                >
                  {sessionIds.map((id) => (
                    <option key={id} value={id}>
                      {id}
                      {id === runningSessionId ? " (running)" : ""}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  onClick={addNewSession}
                  className="shrink-0 px-2 py-1.5 rounded-md border border-zinc-300 dark:border-zinc-600 text-sm hover:bg-zinc-100 dark:hover:bg-zinc-800"
                >
                  New
                </button>
                <span className="text-xs text-zinc-500 dark:text-zinc-400 shrink-0">Mode</span>
                <select
                  value={mode}
                  onChange={(e) => setMode(e.target.value as "direct" | "planner")}
                  disabled={isRunning}
                  className="rounded-md border border-zinc-300 dark:border-zinc-600 px-2 py-1.5 text-sm bg-background text-foreground shrink-0 focus:outline-none focus:ring-2 focus:ring-zinc-400"
                >
                  <option value="direct">Direct</option>
                  <option value="planner">Planner</option>
                </select>
              </div>
              <div className="flex-1 min-w-[200px] flex gap-2">
                <textarea
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      onSend();
                    }
                  }}
                  placeholder="Describe your task… (Shift+Enter for new line)"
                  rows={1}
                  className="flex-1 min-h-[40px] max-h-[120px] resize-y rounded-lg border border-zinc-300 dark:border-zinc-600 px-3 py-2 text-sm bg-background text-foreground placeholder:text-zinc-500 focus:outline-none focus:ring-2 focus:ring-zinc-400 dark:focus:ring-zinc-500 disabled:opacity-50"
                  disabled={!canSend}
                  aria-label="Message"
                />
                <button
                  type="button"
                  onClick={isRunning ? onCancel : onSend}
                  disabled={status !== "connected" || (!input.trim() && !isRunning)}
                  title={
                    status !== "connected"
                      ? "Connecting…"
                      : isRunning
                        ? "Cancel"
                        : !input.trim()
                          ? "Enter a message"
                          : "Send"
                  }
                  className={cn(
                    "shrink-0 h-10 px-4 rounded-lg font-medium flex items-center justify-center gap-1.5",
                    isRunning
                      ? "bg-red-500 hover:bg-red-600 text-white disabled:opacity-50"
                      : "bg-zinc-800 dark:bg-zinc-200 text-zinc-100 dark:text-zinc-900 hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed"
                  )}
                >
                  {isRunning ? (
                    <>
                      <SquareIcon size={16} />
                      Stop
                    </>
                  ) : status === "connecting" ? (
                    <Loader2Icon size={16} />
                  ) : (
                    <>
                      <SendIcon size={16} />
                      Send
                    </>
                  )}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
