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
  planning: { label: "规划中", color: "bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-400" },
  preflight: { label: "预检确认", color: "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-400" },
  executing: { label: "执行中", color: "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-400" },
  replanning: { label: "重新规划", color: "bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-400" },
  completed: { label: "已完成", color: "bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-400" },
  failed: { label: "失败", color: "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-400" },
  aborted: { label: "已中止", color: "bg-gray-100 text-gray-700 dark:bg-gray-900/40 dark:text-gray-400" },
};

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

  return (
    <div className="border border-gray-300 rounded-lg p-3 bg-[#f9fafb] flex flex-col h-full min-h-0">
      <h2 className="text-sm font-semibold mb-2 text-[#1e293b]">状态记录</h2>
      <div className="flex flex-col gap-2 overflow-y-auto overflow-x-hidden flex-1 min-h-0 text-xs break-words">
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
            <div className="font-medium text-[#1e293b]" title="mode 为 direct/planner；此处为实际运行的 Exp 类名，如 DirectSolver、ResearchPlanner、SkillEvolutionExp">
              执行过的 Exp
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
            <div className="font-medium text-[#1e293b]">Hit 到的 Skills</div>
            <ul className="space-y-0.5 list-disc list-inside text-gray-700">
              {skillHits.map((name, i) => (
                <li key={i}>{name}</li>
              ))}
            </ul>
          </>
        )}
        {mode === "direct" && (
          <>
            <div className="font-medium text-[#1e293b]">Direct 模式 · 工具调用</div>
            {toolResults.length === 0 ? (
              <div className="text-gray-500">暂无</div>
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
                Planner · 共 {lastStages.total ?? "?"} 步，当前第 {lastStages.current ?? "?"} 步
              </div>
            )}
            {lastStages?.intent && (
              <div className="text-gray-600 whitespace-pre-wrap break-words">
                当前: {lastStages.intent}
              </div>
            )}
            {/* Replan trigger reason */}
            {lastReplan && lastPhase === "replanning" && (
              <div className="mt-1 p-1.5 rounded bg-purple-50 dark:bg-purple-900/20 border border-purple-200 dark:border-purple-800">
                <div className="font-medium text-purple-700 dark:text-purple-400">重新规划原因</div>
                <div className="text-purple-600 dark:text-purple-300 mt-0.5">
                  {lastReplan.reason ?? "—"}
                  {lastReplan.after_step != null && (
                    <span className="text-purple-500"> (Step {lastReplan.after_step} 之后)</span>
                  )}
                </div>
              </div>
            )}
            {statusSkill.length > 0 && (
              <div className="font-medium text-green-700 mt-1">新生成 Skills</div>
            )}
            {statusSkill.map((e, i) => (
              <div key={i} className="text-green-600 whitespace-pre-wrap break-words">
                • {String(e.content)}
              </div>
            ))}
            {mode === "planner" && !lastStages && statusSkill.length === 0 && !lastPhase && (
              <div className="text-gray-500">规划中或等待执行…</div>
            )}
          </>
        )}
        {mode !== "direct" && mode !== "planner" && expRuns.length === 0 && skillHits.length === 0 && (
          <div className="text-gray-500">暂无</div>
        )}
      </div>
    </div>
  );
}
