"use client";

import React, { useMemo } from "react";
import type { LogEntry } from "./MatMasterView";
import { renderContent } from "./ContentRenderer";
import { cn } from "@/lib/utils";
import { isEnvRelatedEntry } from "@/lib/logEntryUtils";

/**
 * RunOverviewPanel — shown in the left sidebar for **direct** mode.
 * Displays a compact summary of the current run: connection status,
 * tool call counts, latest summary / finish / error.
 */
export default function RunOverviewPanel({
  logs,
  status,
  onJumpToLogIndex,
}: {
  logs: LogEntry[];
  status?: "idle" | "connecting" | "connected" | "closed";
  onJumpToLogIndex?: (index: number) => void;
}) {
  const stats = useMemo(() => {
    let toolCallCount = 0;
    let toolResultCount = 0;
    let toolErrorCount = 0;
    let lastSummary: { entry: LogEntry; index: number } | null = null;
    let lastFinish: { entry: LogEntry; index: number } | null = null;
    let lastError: { entry: LogEntry; index: number } | null = null;
    const toolNames: { name: string; ok: boolean; index: number }[] = [];

    for (let i = 0; i < logs.length; i++) {
      const e = logs[i];
      if (e.type === "tool_call") toolCallCount++;
      if (e.type === "tool_result" && !isEnvRelatedEntry(e)) {
        toolResultCount++;
        const c = e.content as { name?: string; result?: string } | null;
        const r = typeof c?.result === "string" ? c.result : "";
        const ok = !/error|failed|exception|exit code: [1-9]|non-zero exit/i.test(r);
        if (!ok) toolErrorCount++;
        toolNames.push({ name: c?.name ?? "tool", ok, index: i });
      }
      if (e.type === "execution_summary") lastSummary = { entry: e, index: i };
      if (e.type === "finish") lastFinish = { entry: e, index: i };
      if (e.type === "error") lastError = { entry: e, index: i };
    }

    return { toolCallCount, toolResultCount, toolErrorCount, lastSummary, lastFinish, lastError, toolNames };
  }, [logs]);

  const statusLabel = status ?? "idle";
  const statusColor =
    statusLabel === "connected"
      ? "bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400"
      : statusLabel === "connecting"
        ? "bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400"
        : "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400";

  return (
    <div className="p-3 space-y-3 text-xs">
      {/* Connection status */}
      <div className="flex items-center gap-2">
        <span className="text-zinc-500 dark:text-zinc-400 font-medium">Status</span>
        <span
          className={cn(
            "inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-semibold",
            statusColor
          )}
        >
          <span className="inline-block w-1.5 h-1.5 rounded-full bg-current" />
          {statusLabel}
        </span>
      </div>

      {/* Tool call stats */}
      {stats.toolResultCount > 0 && (
        <div className="space-y-1">
          <div className="font-medium text-zinc-700 dark:text-zinc-300">
            Tools ({stats.toolResultCount} call{stats.toolResultCount !== 1 ? "s" : ""}
            {stats.toolErrorCount > 0 && (
              <span className="text-amber-600 dark:text-amber-400">
                , {stats.toolErrorCount} error{stats.toolErrorCount !== 1 ? "s" : ""}
              </span>
            )}
            )
          </div>
          <ul className="space-y-0.5 max-h-[120px] overflow-y-auto">
            {stats.toolNames.map((t, i) => (
              <li key={i} className="flex items-center gap-1.5">
                <span
                  className={cn(
                    "inline-block w-1.5 h-1.5 rounded-full shrink-0",
                    t.ok ? "bg-emerald-500" : "bg-red-500"
                  )}
                />
                <button
                  type="button"
                  className="text-left hover:underline underline-offset-2 truncate text-zinc-600 dark:text-zinc-400"
                  onClick={() => onJumpToLogIndex?.(t.index)}
                  title="Jump to log"
                >
                  {t.name}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Latest summary */}
      {stats.lastSummary && (
        <button
          type="button"
          className="w-full text-left p-2 rounded-md bg-emerald-50/50 dark:bg-emerald-900/20 border border-emerald-200 dark:border-emerald-800 hover:bg-emerald-50 dark:hover:bg-emerald-900/30 transition-colors"
          onClick={() => onJumpToLogIndex?.(stats.lastSummary!.index)}
        >
          <div className="text-[10px] uppercase tracking-wider font-bold text-emerald-700 dark:text-emerald-400 mb-1">
            ✓ Summary
          </div>
          <div className="text-zinc-700 dark:text-zinc-300 line-clamp-3">
            {renderContent(stats.lastSummary.entry.content)}
          </div>
        </button>
      )}

      {/* Latest error */}
      {stats.lastError && (
        <button
          type="button"
          className="w-full text-left p-2 rounded-md bg-red-50/50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 hover:bg-red-50 dark:hover:bg-red-900/30 transition-colors"
          onClick={() => onJumpToLogIndex?.(stats.lastError!.index)}
        >
          <div className="text-[10px] uppercase tracking-wider font-bold text-red-700 dark:text-red-400 mb-1">
            ✗ Error
          </div>
          <div className="text-zinc-700 dark:text-zinc-300 line-clamp-2">
            {String(stats.lastError.entry.content)}
          </div>
        </button>
      )}

      {/* Latest finish */}
      {stats.lastFinish && !stats.lastError && (
        <div className="p-2 rounded-md bg-green-50/50 dark:bg-green-900/20 border border-green-200 dark:border-green-800">
          <div className="text-[10px] uppercase tracking-wider font-bold text-green-700 dark:text-green-400 mb-1">
            ✓ Finished
          </div>
          <div className="text-zinc-700 dark:text-zinc-300 line-clamp-2">
            {String(stats.lastFinish.entry.content) || "Done"}
          </div>
        </div>
      )}

      {/* Empty state */}
      {stats.toolResultCount === 0 && !stats.lastSummary && !stats.lastFinish && !stats.lastError && (
        <p className="text-zinc-500 dark:text-zinc-400 italic">No activity yet.</p>
      )}
    </div>
  );
}
