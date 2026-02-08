"use client";

import { useState } from "react";
import {
  ChevronDownIcon,
  ChevronRightIcon,
  FileCodeIcon,
  ActivityIcon,
  ListOrderedIcon,
  CircleDotIcon,
  CircleCheckIcon,
  CircleXIcon,
} from "./icons";
import { cn } from "@/lib/utils";
import FileTree from "./FileTree";
import type { LogEntry } from "./LogStream";
import { renderContent } from "./ContentRenderer";

function isEnvRelatedToolResult(entry: LogEntry): boolean {
  if (entry.type !== "tool_result" || !entry.content || typeof entry.content !== "object")
    return false;
  const c = entry.content as { name?: string; result?: string; command?: string; args?: string };
  const s = [c.name, c.result, c.command, typeof c.args === "string" ? c.args : ""]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  return s.includes("env");
}

function inferToolSuccess(entry: LogEntry): boolean {
  if (entry.type !== "tool_result" || !entry.content || typeof entry.content !== "object")
    return true;
  const c = entry.content as { result?: string };
  const r = typeof c.result === "string" ? c.result : "";
  if (/error|failed|exception|exit code: [1-9]|non-zero exit/i.test(r)) return false;
  return true;
}

function AccordionSection({
  title,
  icon: Icon,
  defaultOpen = false,
  children,
}: {
  title: string;
  icon: React.ElementType;
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="border border-zinc-200 dark:border-zinc-800 rounded-md overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-2 px-3 py-2 text-left text-sm font-medium text-zinc-700 dark:text-zinc-300 bg-zinc-50 dark:bg-zinc-900/50 hover:bg-zinc-100 dark:hover:bg-zinc-800/50 transition-colors"
      >
        {open ? (
          <ChevronDownIcon size={16} className="shrink-0" />
        ) : (
          <ChevronRightIcon size={16} className="shrink-0" />
        )}
        <Icon size={16} className="shrink-0 text-zinc-500" />
        {title}
      </button>
      {open && <div className="border-t border-zinc-200 dark:border-zinc-800">{children}</div>}
    </div>
  );
}

function renderLogEntry(entry: LogEntry): React.ReactNode {
  if (entry.type === "planner_reply" && typeof entry.content === "string") {
    return <div className="text-sm text-zinc-600 dark:text-zinc-400">Planner: {entry.content}</div>;
  }
  if (entry.type === "tool_result" && entry.content && typeof entry.content === "object") {
    const c = entry.content as { name?: string; result?: string };
    return (
      <div className="space-y-1">
        {c.name && (
          <div className="text-xs font-medium text-zinc-500 dark:text-zinc-400">{c.name}</div>
        )}
        {renderContent(typeof c.result === "string" ? c.result : c)}
      </div>
    );
  }
  return renderContent(entry.content);
}

export default function WorkspacePanel({
  entries,
  sessionId,
  filePath,
  onFilePathChange,
  sessionFilesLogsKey = 0,
  readOnly = false,
}: {
  entries: LogEntry[];
  sessionId: string | null;
  filePath: string;
  onFilePathChange: (path: string) => void;
  sessionFilesLogsKey?: number;
  readOnly?: boolean;
}) {
  const toolResults = entries.filter(
    (e) =>
      e.source === "ToolExecutor" &&
      e.type === "tool_result" &&
      !isEnvRelatedToolResult(e)
  );
  const statusStages = entries.filter((e) => e.type === "status_stages");
  const statusSkill = entries.filter((e) => e.type === "status_skill_produced");
  const skillHits = entries.filter((e) => e.type === "skill_hit").map((e) => String(e.content ?? ""));
  const expRuns = entries.filter((e) => e.type === "exp_run").map((e) => String(e.content ?? ""));
  const lastStages =
    statusStages.length > 0
      ? (statusStages[statusStages.length - 1].content as {
          total?: number;
          current?: number;
          step_id?: number;
          intent?: string;
        })
      : null;
  const mode =
    statusStages.length > 0 || entries.some((e) => e.source === "Planner") ? "planner" : "direct";

  const timelineEntries = entries.filter(
    (e) =>
      (e.source === "Planner" && e.type === "planner_reply") ||
      (e.source === "ToolExecutor" && e.type === "tool_result" && !isEnvRelatedToolResult(e))
  );

  return (
    <div className="flex flex-col h-full min-h-0 bg-card border-r border-zinc-200 dark:border-zinc-800">
      <div className="flex-shrink-0 px-3 py-2 border-b border-zinc-200 dark:border-zinc-800">
        <h2 className="text-sm font-semibold text-zinc-800 dark:text-zinc-200">Workspace</h2>
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto p-3 space-y-3">
        {!readOnly && sessionId && (
          <AccordionSection title="Files" icon={FileCodeIcon} defaultOpen={true}>
            <div className="p-2 max-h-[240px] overflow-y-auto">
              <FileTree
                key={`${sessionId}-${sessionFilesLogsKey}`}
                sessionId={sessionId}
                filePath={filePath}
                onFilePathChange={onFilePathChange}
                compact
              />
            </div>
          </AccordionSection>
        )}

        <AccordionSection title="Status" icon={ActivityIcon} defaultOpen={true}>
          <div className="p-3 text-xs space-y-2 text-zinc-600 dark:text-zinc-400">
            {expRuns.length > 0 && (
              <>
                <div className="font-medium text-zinc-700 dark:text-zinc-300">Executions</div>
                <ul className="list-disc list-inside space-y-0.5">
                  {expRuns.map((name, i) => (
                    <li key={i}>{name}</li>
                  ))}
                </ul>
              </>
            )}
            {skillHits.length > 0 && (
              <>
                <div className="font-medium text-zinc-700 dark:text-zinc-300">Skills hit</div>
                <ul className="list-disc list-inside space-y-0.5">
                  {skillHits.map((name, i) => (
                    <li key={i}>{name}</li>
                  ))}
                </ul>
              </>
            )}
            {mode === "direct" && (
              <>
                <div className="font-medium text-zinc-700 dark:text-zinc-300">Tools</div>
                {toolResults.length === 0 ? (
                  <p className="text-zinc-500">—</p>
                ) : (
                  <ul className="space-y-0.5">
                    {toolResults.map((e, i) => {
                      const c = e.content as { name?: string };
                      const ok = inferToolSuccess(e);
                      return (
                        <li
                          key={i}
                          className={cn(
                            "flex items-center gap-1.5",
                            ok ? "text-zinc-600 dark:text-zinc-400" : "text-amber-600 dark:text-amber-400"
                          )}
                        >
                          {ok ? (
                            <CircleCheckIcon size={14} className="shrink-0 text-emerald-500" />
                          ) : (
                            <CircleXIcon size={14} className="shrink-0" />
                          )}
                          {c?.name ?? "—"}
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
                  <div className="font-medium text-zinc-700 dark:text-zinc-300">
                    Planner · Step {lastStages.current ?? "?"} / {lastStages.total ?? "?"}
                  </div>
                )}
                {lastStages?.intent && (
                  <p className="whitespace-pre-wrap break-words">{lastStages.intent}</p>
                )}
                {statusSkill.length > 0 && (
                  <div className="font-medium text-emerald-600 dark:text-emerald-400">
                    New skills
                  </div>
                )}
                {statusSkill.map((e, i) => (
                  <div key={i} className="text-emerald-600 dark:text-emerald-400 whitespace-pre-wrap">
                    • {String(e.content)}
                  </div>
                ))}
                {!lastStages && statusSkill.length === 0 && (
                  <p className="text-zinc-500">Planning or waiting…</p>
                )}
              </>
            )}
            {expRuns.length === 0 && skillHits.length === 0 && mode !== "planner" && (
              <p className="text-zinc-500">—</p>
            )}
          </div>
        </AccordionSection>

        <AccordionSection title="Execution log" icon={ListOrderedIcon}>
          <div className="p-2">
            {timelineEntries.length === 0 ? (
              <p className="text-xs text-zinc-500 py-2">No steps yet.</p>
            ) : (
              <ul className="space-y-0">
                {timelineEntries.map((entry, i) => {
                  const isTool =
                    entry.source === "ToolExecutor" && entry.type === "tool_result";
                  const ok = isTool ? inferToolSuccess(entry) : true;
                  return (
                    <li key={i} className="flex gap-2 py-2 border-b border-zinc-100 dark:border-zinc-800 last:border-0">
                      <span className="shrink-0 mt-0.5">
                        {isTool ? (
                          ok ? (
                            <CircleCheckIcon size={14} className="text-emerald-500" />
                          ) : (
                            <CircleXIcon size={14} className="text-red-500" />
                          )
                        ) : (
                          <CircleDotIcon size={14} className="text-zinc-400" />
                        )}
                      </span>
                      <div className="min-w-0 flex-1 text-xs">
                        <div className="font-medium text-zinc-600 dark:text-zinc-400 mb-0.5">
                          {entry.source}
                        </div>
                        <div className="text-zinc-700 dark:text-zinc-300 break-words">
                          {renderLogEntry(entry)}
                        </div>
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </AccordionSection>
      </div>
    </div>
  );
}
