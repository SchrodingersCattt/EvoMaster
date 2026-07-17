"use client";

import type { LogEntry } from "./LogStream";
import { isEnvRelatedEntry } from "@/lib/logEntryUtils";

function inferToolSuccess(entry: LogEntry): boolean {
  if (entry.type !== "tool_result" || !entry.content || typeof entry.content !== "object") return true;
  const c = entry.content as { result?: string };
  const r = typeof c.result === "string" ? c.result : "";
  if (/\berror\b|\bfailed\b|\bexception\b|exit code: [1-9]|non-zero exit/i.test(r)) return false;
  return true;
}

const PHASE_LABELS: Record<string, { label: string; color: string }> = {
  planning: { label: "Planning", color: "bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-400" },
  preflight: { label: "Preflight", color: "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-400" },
  executing: { label: "Executing", color: "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-400" },
  replanning: { label: "Replanning", color: "bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-400" },
  completed: { label: "Completed", color: "bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-400" },
  failed: { label: "Failed", color: "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-400" },
  aborted: { label: "Aborted", color: "bg-gray-100 text-gray-700 dark:bg-gray-900/40 dark:text-gray-400" },
};

// Context compaction event payload shape
interface CompactionPayload {
  status: "started" | "finished" | "skipped" | "failed";
  tokens_before?: number;
  tokens_after?: number;
  tokens_saved?: number;
  saved_ratio?: number;
  compressed_turns?: number;
  recent_msgs_kept?: number;
  duration_ms?: number;
  reason?: string;
  trigger_tokens?: number;
  compressible_ratio?: number;
}

export default function StatusPanel({ entries }: { entries: LogEntry[] }) {
  const toolResults = entries.filter(
    (e) => e.source === "ToolExecutor" && e.type === "tool_result" && !isEnvRelatedEntry(e)
  );
  const statusStages = entries.filter((e) => e.type === "status_stages");
  const statusSkill = entries.filter((e) => e.type === "status_skill_produced");
  const skillHits = entries.filter((e) => e.type === "skill_hit").map((e) => String(e.content ?? ""));
  const expRuns = entries.filter((e) => e.type === "exp_run").map((e) => String(e.content ?? ""));
  const lastStages = statusStages.length > 0 ? (statusStages[statusStages.length - 1].content as { total?: number; current?: number; step_id?: number; intent?: string }) : null;
  const mode = statusStages.length > 0 || entries.some((e) => e.source === "Planner") ? "planner" : "direct";

  // Dynamic closed-loop planning events
  const phaseChanges = entries.filter((e) => e.type === "phase_change");
  const replanEvents = entries.filter((e) => e.type === "replan_triggered");
  const planRevisions = entries.filter((e) => e.type === "plan_revised");
  const lastPhase = phaseChanges.length > 0
    ? (phaseChanges[phaseChanges.length - 1].content as { from?: string; to?: string })?.to ?? ""
    : "";
  const lastReplan = replanEvents.length > 0
    ? (replanEvents[replanEvents.length - 1].content as { reason?: string; after_step?: number })
    : null;
  const replanCount = planRevisions.length;

  // Context compaction events
  const compactionEvents = entries
    .filter((e) => e.type === "context_compaction")
    .map((e) => e.content as CompactionPayload);
  const lastCompaction = compactionEvents.length > 0 ? compactionEvents[compactionEvents.length - 1] : null;
  const finishedCompactions = compactionEvents.filter((c) => c.status === "finished");
  const compactionCount = finishedCompactions.length;

  return (
    <div className="border border-gray-300 rounded-lg p-3 bg-[#f9fafb] flex flex-col h-full min-h-0">
      <h2 className="text-sm font-semibold mb-2 text-[#1e293b]">Status</h2>
      <div className="flex flex-col gap-2 overflow-y-auto overflow-x-hidden flex-1 min-h-0 text-xs break-words">

        {/* Context compaction status */}
        {lastCompaction && (
          <div className={`rounded p-1.5 border text-[10px] ${
            lastCompaction.status === "started"
              ? "bg-amber-50 border-amber-200 text-amber-700 dark:bg-amber-900/20 dark:border-amber-800 dark:text-amber-400"
              : lastCompaction.status === "finished"
              ? "bg-sky-50 border-sky-200 text-sky-700 dark:bg-sky-900/20 dark:border-sky-800 dark:text-sky-400"
              : lastCompaction.status === "failed"
              ? "bg-red-50 border-red-200 text-red-700 dark:bg-red-900/20 dark:border-red-800 dark:text-red-400"
              : "bg-gray-50 border-gray-200 text-gray-500"
          }`}>
            <div className="font-semibold mb-0.5">
              {lastCompaction.status === "started" && "🔄 Compacting context…"}
              {lastCompaction.status === "finished" && `✅ Context compacted ×${compactionCount}`}
              {lastCompaction.status === "skipped" && "⏭ Skipped compaction (insufficient ratio)"}
              {lastCompaction.status === "failed" && "⚠️ Compaction failed, degraded"}
            </div>
            {lastCompaction.status === "finished" && lastCompaction.tokens_before != null && lastCompaction.tokens_after != null && (
              <div className="text-sky-600 dark:text-sky-300">
                {lastCompaction.tokens_before.toLocaleString()} → {lastCompaction.tokens_after.toLocaleString()} tokens
                {lastCompaction.saved_ratio != null && (
                  <span className="ml-1 font-medium">
                    (saved {(lastCompaction.saved_ratio * 100).toFixed(0)}%
                    {lastCompaction.duration_ms != null && `, ${(lastCompaction.duration_ms / 1000).toFixed(1)}s`})
                  </span>
                )}
              </div>
            )}
            {lastCompaction.status === "failed" && lastCompaction.reason && (
              <div className="text-red-500 mt-0.5 truncate" title={lastCompaction.reason}>
                {lastCompaction.reason}
              </div>
            )}
          </div>
        )}

        {/* Phase badge for planner mode */}
        {mode === "planner" && lastPhase && (
          <div className="flex items-center gap-2 flex-wrap">
            <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-semibold ${PHASE_LABELS[lastPhase]?.color ?? "bg-gray-100 text-gray-700"}`}>
              {PHASE_LABELS[lastPhase]?.label ?? lastPhase}
            </span>
            {replanCount > 0 && (
              <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-semibold bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-400">
                Replan ×{replanCount}
              </span>
            )}
          </div>
        )}

        {expRuns.length > 0 && (
          <>
            <div className="font-medium text-[#1e293b]" title="mode is direct/planner; shows the actual Exp class name, e.g. DirectSolver, ResearchPlanner, SkillEvolutionExp">
              Experiments Run
            </div>
            <ul className="space-y-0.5 list-disc list-inside text-gray-700">
              {expRuns.map((name, i) => (
                <li key={i}>{name}</li>
              ))}
            </ul>
          </>
        )}
        {skillHits.length > 0 && (
          <>
            <div className="font-medium text-[#1e293b]">Matched Skills</div>
            <ul className="space-y-0.5 list-disc list-inside text-gray-700">
              {skillHits.map((name, i) => (
                <li key={i}>{name}</li>
              ))}
            </ul>
          </>
        )}
        {mode === "direct" && (
          <>
            <div className="font-medium text-[#1e293b]">Direct Mode · Tool Calls</div>
            {toolResults.length === 0 ? (
              <div className="text-gray-500">None</div>
            ) : (
              <ul className="space-y-1 list-disc list-inside">
                {toolResults.map((e, i) => {
                  const c = e.content as { name?: string };
                  const ok = inferToolSuccess(e);
                  return (
                    <li key={i} className={ok ? "text-gray-700" : "text-amber-700"}>
                      {c?.name ?? "—"} {ok ? "✓" : "✗"}
                    </li>
                  );
                })}
              </ul>
            )}
          </>
        )}
        {mode === "planner" && (
          <>
            {lastStages && (
              <div className="font-medium text-[#1e293b]">
                Planner · {lastStages.total ?? "?"} steps, current step {lastStages.current ?? "?"}
              </div>
            )}
            {lastStages?.intent && (
              <div className="text-gray-600 whitespace-pre-wrap break-words">
                Current: {lastStages.intent}
              </div>
            )}
            {/* Replan trigger reason */}
            {lastReplan && lastPhase === "replanning" && (
              <div className="mt-1 p-1.5 rounded bg-purple-50 dark:bg-purple-900/20 border border-purple-200 dark:border-purple-800">
                <div className="font-medium text-purple-700 dark:text-purple-400">Replan Reason</div>
                <div className="text-purple-600 dark:text-purple-300 mt-0.5">
                  {lastReplan.reason ?? "—"}
                  {lastReplan.after_step != null && (
                    <span className="text-purple-500"> (after Step {lastReplan.after_step})</span>
                  )}
                </div>
              </div>
            )}
            {statusSkill.length > 0 && (
              <div className="font-medium text-green-700 mt-1">Newly Generated Skills</div>
            )}
            {statusSkill.map((e, i) => (
              <div key={i} className="text-green-600 whitespace-pre-wrap break-words">
                • {String(e.content)}
              </div>
            ))}
            {mode === "planner" && !lastStages && statusSkill.length === 0 && !lastPhase && (
              <div className="text-gray-500">Planning or awaiting execution…</div>
            )}
          </>
        )}
        {mode !== "direct" && mode !== "planner" && expRuns.length === 0 && skillHits.length === 0 && (
          <div className="text-gray-500">None</div>
        )}
      </div>
    </div>
  );
}
