"use client";

import type { LogEntry } from "./LogStream";

function isEnvRelatedToolResult(entry: LogEntry): boolean {
  if (entry.type !== "tool_result" || !entry.content || typeof entry.content !== "object") return false;
  const c = entry.content as { name?: string; result?: string; command?: string; args?: string };
  const s = [c.name, c.result, c.command, typeof c.args === "string" ? c.args : ""].filter(Boolean).join(" ").toLowerCase();
  return s.includes("env");
}

function inferToolSuccess(entry: LogEntry): boolean {
  if (entry.type !== "tool_result" || !entry.content || typeof entry.content !== "object") return true;
  const c = entry.content as { result?: string };
  const r = typeof c.result === "string" ? c.result : "";
  if (/error|failed|exception|exit code: [1-9]|non-zero exit/i.test(r)) return false;
  return true;
}

export default function StatusPanel({ entries }: { entries: LogEntry[] }) {
  const toolResults = entries.filter(
    (e) => e.source === "ToolExecutor" && e.type === "tool_result" && !isEnvRelatedToolResult(e)
  );
  const statusStages = entries.filter((e) => e.type === "status_stages");
  const statusSkill = entries.filter((e) => e.type === "status_skill_produced");
  const lastStages = statusStages.length > 0 ? (statusStages[statusStages.length - 1].content as { total?: number; current?: number; step_id?: number; intent?: string }) : null;
  const mode = statusStages.length > 0 || entries.some((e) => e.source === "Planner") ? "planner" : "direct";

  return (
    <div className="border border-gray-300 rounded-lg p-3 bg-[#f9fafb] flex flex-col h-full min-h-0">
      <h2 className="text-sm font-semibold mb-2 text-[#1e293b]">状态记录</h2>
      <div className="flex flex-col gap-2 overflow-y-auto flex-1 min-h-0 text-xs">
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
              <div className="text-gray-600 truncate" title={lastStages.intent}>
                当前: {lastStages.intent}
              </div>
            )}
            {statusSkill.length > 0 && (
              <div className="font-medium text-green-700 mt-1">新生成 Skills</div>
            )}
            {statusSkill.map((e, i) => (
              <div key={i} className="text-green-600 truncate" title={String(e.content)}>
                • {String(e.content)}
              </div>
            ))}
            {mode === "planner" && !lastStages && statusSkill.length === 0 && (
              <div className="text-gray-500">规划中或等待执行…</div>
            )}
          </>
        )}
        {mode !== "direct" && mode !== "planner" && <div className="text-gray-500">暂无</div>}
      </div>
    </div>
  );
}
