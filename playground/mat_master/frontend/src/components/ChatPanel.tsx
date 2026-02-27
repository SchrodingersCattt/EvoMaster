"use client";

import { useRef, useEffect, useState } from "react";
import { SendIcon, SquareIcon, Loader2Icon } from "./icons";
import { cn } from "@/lib/utils";
import type { LogEntry } from "./LogStream";
import { renderContent } from "./ContentRenderer";
import { isEnvRelatedEntry } from "@/lib/logEntryUtils";

// ─── Helpers ────────────────────────────────────────────────────────────────

/** Checks whether a thought event has no meaningful content. */
function isEmptyThought(entry: LogEntry): boolean {
  if (entry.type !== "thought") return false;
  if (entry.content === null || entry.content === undefined) return true;
  if (typeof entry.content === "string" && !entry.content.trim()) return true;
  return false;
}

/** Event types rendered as centered, slim status indicators rather than bubbles. */
const STATUS_EVENT_TYPES = new Set([
  "phase_change",
  "exp_run",
  "finish",
  "error",
  "cancelled",
  "skill_hit",
  "replan_triggered",
  "plan_revised",
  "status_stages",
  "status_skill_produced",
]);

function isStatusEvent(entry: LogEntry): boolean {
  return STATUS_EVENT_TYPES.has(entry.type);
}

function ToolCard({
  title,
  content,
  isResult = false,
}: {
  title: string;
  content: unknown;
  isResult?: boolean;
}) {
  return (
    <div className="my-2 rounded-md border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 overflow-hidden shadow-sm">
      {isResult ? (
        <>
          <div className="flex items-center px-3 py-1.5 bg-zinc-50 dark:bg-zinc-800/50 border-b border-zinc-100 dark:border-zinc-800">
            <span className="text-[10px] uppercase tracking-wider font-semibold text-zinc-500 dark:text-zinc-400">
              Output
            </span>
            <span className="ml-2 text-xs font-mono text-zinc-700 dark:text-zinc-300 truncate">
              {title}
            </span>
          </div>
          <div
            className={cn(
              "max-h-60 overflow-y-auto p-3 text-xs font-mono",
              "scrollbar-thin scrollbar-thumb-zinc-300 dark:scrollbar-thumb-zinc-600 scrollbar-track-transparent",
              "[&::-webkit-scrollbar]:w-1.5",
              "[&::-webkit-scrollbar-track]:bg-transparent",
              "[&::-webkit-scrollbar-thumb]:bg-zinc-300/50 dark:[&::-webkit-scrollbar-thumb]:bg-zinc-600/50",
              "[&::-webkit-scrollbar-thumb]:rounded-full",
              "[&::-webkit-scrollbar-thumb]:hover:bg-zinc-400 dark:[&::-webkit-scrollbar-thumb]:hover:bg-zinc-500"
            )}
          >
            {renderContent(content)}
          </div>
        </>
      ) : (
        <details>
          <summary className="list-none flex items-center px-3 py-1.5 bg-zinc-50 dark:bg-zinc-800/50 border-b border-zinc-100 dark:border-zinc-800 cursor-pointer">
            <span className="text-[10px] uppercase tracking-wider font-semibold text-zinc-500 dark:text-zinc-400">
              Call
            </span>
            <span className="ml-2 text-xs font-mono text-zinc-700 dark:text-zinc-300 truncate">
              {title}
            </span>
            <span className="ml-auto text-zinc-400 text-xs">展开</span>
          </summary>
          <div
            className={cn(
              "max-h-60 overflow-y-auto p-3 text-xs font-mono",
              "scrollbar-thin scrollbar-thumb-zinc-300 dark:scrollbar-thumb-zinc-600 scrollbar-track-transparent",
              "[&::-webkit-scrollbar]:w-1.5",
              "[&::-webkit-scrollbar-track]:bg-transparent",
              "[&::-webkit-scrollbar-thumb]:bg-zinc-300/50 dark:[&::-webkit-scrollbar-thumb]:bg-zinc-600/50",
              "[&::-webkit-scrollbar-thumb]:rounded-full",
              "[&::-webkit-scrollbar-thumb]:hover:bg-zinc-400 dark:[&::-webkit-scrollbar-thumb]:hover:bg-zinc-500"
            )}
          >
            {renderContent(content)}
          </div>
        </details>
      )}
    </div>
  );
}

// ─── StatusEvent: centered slim banners for meta / system events ────────────

const PHASE_COLORS: Record<string, string> = {
  init: "bg-zinc-100 text-zinc-600 dark:bg-zinc-800/50 dark:text-zinc-400",
  pre_check: "bg-amber-50 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400",
  planning: "bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400",
  preflight: "bg-amber-50 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400",
  executing: "bg-emerald-50 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400",
  replanning: "bg-purple-50 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400",
  completed: "bg-green-50 text-green-700 dark:bg-green-900/30 dark:text-green-400",
  failed: "bg-red-50 text-red-700 dark:bg-red-900/30 dark:text-red-400",
  aborted: "bg-zinc-100 text-zinc-600 dark:bg-zinc-800/50 dark:text-zinc-400",
};

function StatusPill({ className, children }: { className?: string; children: React.ReactNode }) {
  return (
    <div className="flex justify-center my-1">
      <span
        className={cn(
          "inline-flex items-center gap-1.5 text-[11px] px-3 py-1 rounded-full font-medium max-w-[90%] truncate",
          className ?? "bg-zinc-100 text-zinc-500 dark:bg-zinc-800/50 dark:text-zinc-400"
        )}
      >
        {children}
      </span>
    </div>
  );
}

function StatusEvent({ entry }: { entry: LogEntry }) {
  if (entry.type === "phase_change") {
    const c = entry.content as { from?: string; to?: string } | null;
    const to = String(c?.to ?? "");
    return (
      <StatusPill className={PHASE_COLORS[to]}>
        {c?.from ?? "?"} &rarr; {to}
      </StatusPill>
    );
  }
  if (entry.type === "exp_run") {
    return (
      <StatusPill className="bg-indigo-50 text-indigo-700 dark:bg-indigo-900/30 dark:text-indigo-400">
        &#9654; {String(entry.content)}
      </StatusPill>
    );
  }
  if (entry.type === "finish") {
    return (
      <StatusPill className="bg-emerald-50 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400">
        &#10003; {String(entry.content) || "Done"}
      </StatusPill>
    );
  }
  if (entry.type === "error") {
    return (
      <StatusPill className="bg-red-50 text-red-700 dark:bg-red-900/30 dark:text-red-400">
        &#10007; {String(entry.content)}
      </StatusPill>
    );
  }
  if (entry.type === "cancelled") {
    return (
      <StatusPill className="bg-zinc-100 text-zinc-600 dark:bg-zinc-800/50 dark:text-zinc-400">
        &#9724; {String(entry.content) || "Cancelled"}
      </StatusPill>
    );
  }
  if (entry.type === "skill_hit") {
    return (
      <StatusPill className="bg-teal-50 text-teal-700 dark:bg-teal-900/30 dark:text-teal-400">
        &#9889; Skill: {String(entry.content)}
      </StatusPill>
    );
  }
  if (entry.type === "replan_triggered") {
    const c = entry.content as { reason?: string; after_step?: number } | null;
    return (
      <StatusPill className="bg-purple-50 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400">
        &#8635; Replan{c?.after_step != null ? ` (step ${c.after_step})` : ""}: {c?.reason ?? ""}
      </StatusPill>
    );
  }
  if (entry.type === "plan_revised") {
    const c = entry.content as { old_step_count?: number; new_step_count?: number; replan_count?: number } | null;
    return (
      <StatusPill className="bg-purple-50 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400">
        &#8635; Plan revised: {c?.old_step_count ?? "?"} &rarr; {c?.new_step_count ?? "?"} steps
      </StatusPill>
    );
  }
  if (entry.type === "status_stages") {
    const c = entry.content as { total?: number; current?: number; intent?: string } | null;
    return (
      <StatusPill className="bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400">
        Step {c?.current ?? "?"}/{c?.total ?? "?"}{c?.intent ? `: ${c.intent}` : ""}
      </StatusPill>
    );
  }
  if (entry.type === "status_skill_produced") {
    return (
      <StatusPill className="bg-emerald-50 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400">
        &#10022; Skill produced: {String(entry.content)}
      </StatusPill>
    );
  }
  // Fallback for unknown status events
  return (
    <StatusPill>
      {entry.source}: {entry.type}
    </StatusPill>
  );
}

// ─── Tool payload parsers ───────────────────────────────────────────────────

function parseToolPayload(content: unknown): { name: string; args?: unknown } | null {
  if (content == null) return null;
  if (typeof content === "string") {
    try {
      const parsed = JSON.parse(content) as { name?: string; args?: unknown };
      return { name: parsed?.name ?? "tool", args: parsed?.args };
    } catch {
      return { name: "tool", args: content };
    }
  }
  if (typeof content === "object") {
    const c = content as { name?: string; args?: unknown };
    return { name: c?.name ?? "tool", args: c?.args };
  }
  return null;
}

function parseToolResultPayload(content: unknown): { name: string; result?: unknown } | null {
  if (content == null) return null;
  if (typeof content === "string") {
    try {
      const parsed = JSON.parse(content) as { name?: string; result?: unknown };
      return { name: parsed?.name ?? "result", result: parsed?.result };
    } catch {
      return { name: "result", result: content };
    }
  }
  if (typeof content === "object") {
    const c = content as { name?: string; result?: unknown };
    return { name: c?.name ?? "result", result: c?.result };
  }
  return null;
}

function renderEntry(entry: LogEntry): React.ReactNode {
  if (entry.type === "planner_reply" && typeof entry.content === "string") {
    return <div className="text-sm">Planner: {entry.content}</div>;
  }
  if (entry.type === "tool_call") {
    const payload = parseToolPayload(entry.content);
    if (payload) {
      return (
        <ToolCard title={payload.name} content={payload.args} isResult={false} />
      );
    }
  }
  if (entry.type === "tool_result") {
    const payload = parseToolResultPayload(entry.content);
    if (payload) {
      return (
        <ToolCard title={payload.name} content={payload.result} isResult={true} />
      );
    }
  }
  if (entry.type === "execution_summary") {
    return (
      <div className="my-2 rounded-md border border-emerald-200 dark:border-emerald-800 bg-emerald-50/50 dark:bg-emerald-900/20 p-3">
        <div className="text-[10px] uppercase tracking-wider font-semibold text-emerald-600 dark:text-emerald-400 mb-1">
          Summary
        </div>
        <div className="text-sm">{renderContent(entry.content)}</div>
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
  const isThought = entry.type === "thought";
  const hasThoughts =
    source !== "User" &&
    !isThought &&
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

  // Thought events use a collapsible style by default (to avoid clutter)
  if (isThought) {
    return (
      <div className="flex w-full justify-start">
        <div className="max-w-[85%] rounded-lg px-3 py-2 shadow-sm border border-violet-200 dark:border-violet-800 bg-violet-50/60 dark:bg-violet-900/20">
          <details className="group">
            <summary className="list-none flex items-center gap-1.5 cursor-pointer text-xs font-medium text-violet-600 dark:text-violet-400">
              <span className="inline-block transition group-open:rotate-90 text-[10px]">&#9654;</span>
              {source} &middot; Thinking
            </summary>
            <div className="mt-1.5 text-sm text-zinc-700 dark:text-zinc-300 break-words whitespace-pre-wrap">
              {mainContent}
            </div>
          </details>
        </div>
      </div>
    );
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
              <span className="inline-block transition group-open:rotate-90">&#8250;</span>
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
  askHumanQuestion,
  askHumanInput,
  setAskHumanInput,
  sendAskHumanReply,
  askHumanMode = "timeout",
  askHumanTimeoutSec = 20,
  readOnly = false,
  jumpToLogIndex,
  onJumpHandled,
}: {
  entries: LogEntry[];
  scrollRef?: React.RefObject<HTMLDivElement>;
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
  askHumanQuestion: string | null;
  askHumanInput: string;
  setAskHumanInput: (v: string) => void;
  sendAskHumanReply: (content: string) => void;
  askHumanMode?: "timeout" | "block";
  askHumanTimeoutSec?: number;
  readOnly?: boolean;
  jumpToLogIndex?: number | null;
  onJumpHandled?: () => void;
}) {
  const filtered = entries
    .map((e, index) => ({ entry: e, index }))
    .filter(
      ({ entry: e }) =>
      e.type !== "log_line" &&
      e.type !== "status" &&
      !isEnvRelatedEntry(e) &&
      !isEmptyThought(e)
    );
  const isRunning = running && currentSessionId === runningSessionId;
  const canSend = status === "connected" && !isRunning;
  const [highlightIndex, setHighlightIndex] = useState<number | null>(null);

  // Countdown timer for timeout-mode ask-human dialog
  const [countdown, setCountdown] = useState<number | null>(null);
  useEffect(() => {
    if (askHumanQuestion === null || askHumanMode !== "timeout") {
      setCountdown(null);
      return;
    }
    setCountdown(askHumanTimeoutSec);
    const interval = setInterval(() => {
      setCountdown((prev) => {
        if (prev === null || prev <= 1) {
          clearInterval(interval);
          return 0;
        }
        return prev - 1;
      });
    }, 1000);
    return () => clearInterval(interval);
  }, [askHumanQuestion, askHumanMode, askHumanTimeoutSec]);

  // Smart auto-scroll: only scroll to bottom after updates if user was already near bottom.
  const NEAR_BOTTOM_THRESHOLD_PX = 50;
  const isNearBottomRef = useRef(true);

  useEffect(() => {
    const el = scrollRef?.current;
    if (!el) return;
    const updateNearBottom = () => {
      const { scrollTop, scrollHeight, clientHeight } = el;
      isNearBottomRef.current =
        scrollTop + clientHeight >= scrollHeight - NEAR_BOTTOM_THRESHOLD_PX;
    };
    el.addEventListener("scroll", updateNearBottom, { passive: true });
    return () => el.removeEventListener("scroll", updateNearBottom);
  }, [scrollRef]);

  useEffect(() => {
    if (scrollRef?.current && isNearBottomRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [filtered.length, scrollRef]);

  useEffect(() => {
    if (jumpToLogIndex === null || jumpToLogIndex === undefined) return;
    const target = document.getElementById(`chat-log-${jumpToLogIndex}`);
    if (target) {
      target.scrollIntoView({ behavior: "smooth", block: "center" });
      setHighlightIndex(jumpToLogIndex);
      window.setTimeout(() => {
        setHighlightIndex((prev) => (prev === jumpToLogIndex ? null : prev));
      }, 1800);
    }
    onJumpHandled?.();
  }, [jumpToLogIndex, onJumpHandled]);

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
            filtered.map(({ entry: log, index }, i) => (
              <div
                key={log.msg_id ?? i}
                id={`chat-log-${index}`}
                className={cn(
                  "rounded-xl transition-colors duration-500",
                  highlightIndex === index && "bg-amber-100/60 dark:bg-amber-500/20"
                )}
              >
                {isStatusEvent(log) ? (
                  <StatusEvent entry={log} />
                ) : (
                  <MessageBubble
                    entry={log}
                    isUser={log.source === "User"}
                  />
                )}
              </div>
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

        {!readOnly && askHumanQuestion !== null && (
          <div className="flex-shrink-0 mx-4 mb-2 p-3 rounded-lg border border-amber-300 dark:border-amber-600 bg-amber-50 dark:bg-amber-950/30">
            <div className="flex items-center justify-between mb-2">
              <div className="text-xs font-medium text-amber-700 dark:text-amber-400">
                Agent needs your input
              </div>
              {askHumanMode === "timeout" && countdown !== null && (
                <div className="text-xs text-amber-600 dark:text-amber-400 tabular-nums">
                  {countdown > 0 ? `Auto-skip in ${countdown}s` : "Timed out"}
                </div>
              )}
              {askHumanMode === "block" && (
                <div className="text-xs text-amber-600 dark:text-amber-400">
                  Waiting for your reply…
                </div>
              )}
            </div>
            {askHumanMode === "timeout" && countdown !== null && (
              <div className="w-full h-1 rounded-full bg-amber-200 dark:bg-amber-800 mb-2 overflow-hidden">
                <div
                  className="h-full rounded-full bg-amber-500 dark:bg-amber-400 transition-all duration-1000 ease-linear"
                  style={{ width: `${Math.max(0, (countdown / askHumanTimeoutSec) * 100)}%` }}
                />
              </div>
            )}
            <div className="text-sm text-zinc-700 dark:text-zinc-300 mb-2 whitespace-pre-wrap">
              {askHumanQuestion}
            </div>
            <div className="flex gap-2 items-center flex-wrap">
              <input
                type="text"
                value={askHumanInput}
                onChange={(e) => setAskHumanInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && askHumanInput.trim()) {
                    sendAskHumanReply(askHumanInput);
                  }
                }}
                placeholder="Type your answer…"
                className="flex-1 min-w-[140px] rounded-md border border-amber-300 dark:border-amber-600 px-2.5 py-1.5 text-sm bg-card text-foreground focus:outline-none focus:ring-2 focus:ring-amber-400 dark:focus:ring-amber-500"
                autoFocus
              />
              <button
                type="button"
                onClick={() => sendAskHumanReply(askHumanInput)}
                disabled={!askHumanInput.trim()}
                className="px-3 py-1.5 rounded-md bg-amber-600 dark:bg-amber-500 text-white text-sm font-medium hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                Reply
              </button>
              <button
                type="button"
                onClick={() => sendAskHumanReply("skip")}
                className="px-3 py-1.5 rounded-md border border-zinc-300 dark:border-zinc-600 text-sm text-zinc-600 dark:text-zinc-400 hover:bg-zinc-100 dark:hover:bg-zinc-800"
              >
                Skip
              </button>
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
