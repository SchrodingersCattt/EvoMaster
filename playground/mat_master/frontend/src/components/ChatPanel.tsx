"use client";

import React, { useRef, useEffect, useState, useMemo, useCallback } from "react";
import { SendIcon, SquareIcon, Loader2Icon } from "./icons";
import { cn } from "@/lib/utils";
import type { LogEntry } from "./LogStream";
import { renderContent, renderMarkdown } from "./ContentRenderer";
import { ExecutionGraphRenderer } from "./ExecutionGraphRenderer";
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

// ─── ToolCard (memo) ────────────────────────────────────────────────────────

const scrollClasses = cn(
  "max-h-60 overflow-y-auto p-3 text-xs font-mono",
  "scrollbar-thin scrollbar-thumb-zinc-300 dark:scrollbar-thumb-zinc-600 scrollbar-track-transparent",
  "[&::-webkit-scrollbar]:w-1.5",
  "[&::-webkit-scrollbar-track]:bg-transparent",
  "[&::-webkit-scrollbar-thumb]:bg-zinc-300/50 dark:[&::-webkit-scrollbar-thumb]:bg-zinc-600/50",
  "[&::-webkit-scrollbar-thumb]:rounded-full",
  "[&::-webkit-scrollbar-thumb]:hover:bg-zinc-400 dark:[&::-webkit-scrollbar-thumb]:hover:bg-zinc-500"
);

// Single call or result card (used when no matching pair found)
const ToolCard = React.memo(function ToolCard({
  title,
  content,
  isResult = false,
  sourceLabel,
}: {
  title: string;
  content: unknown;
  isResult?: boolean;
  sourceLabel?: string;
}) {
  const summaryLabel = isResult ? (
    <span className="text-[10px] uppercase tracking-wider font-semibold text-emerald-600 dark:text-emerald-400">Output</span>
  ) : (
    <span className="text-[10px] uppercase tracking-wider font-semibold text-zinc-500 dark:text-zinc-400">Call</span>
  );

  return (
    <div className="my-1 rounded-md border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 overflow-hidden shadow-sm">
      <details>
        <summary className="list-none flex items-center px-3 py-1.5 bg-zinc-50 dark:bg-zinc-800/50 border-b border-zinc-100 dark:border-zinc-800 cursor-pointer gap-1.5">
          {sourceLabel && (
            <>
              <span className="text-xs font-medium text-zinc-600 dark:text-zinc-300">{sourceLabel}</span>
              <span className="text-zinc-300 dark:text-zinc-600">&middot;</span>
            </>
          )}
          {summaryLabel}
          <span className="ml-1 text-xs font-mono text-zinc-700 dark:text-zinc-300 truncate">{title}</span>
          <span className="ml-auto text-zinc-400 text-xs flex-shrink-0">展开</span>
        </summary>
        <div className={scrollClasses}>{renderContent(content)}</div>
      </details>
    </div>
  );
});

// Paired call+result card — left-border strip style, collapsed by default
// Collapsed: single summary line (tool name + source badges + chevron)
// Expanded: CALL and OUTPUT sub-sections each with their own <details>
const PairedToolCard = React.memo(function PairedToolCard({
  title,
  callSource,
  resultSource,
  callArgs,
  result,
  isLatest = false,
}: {
  title: string;
  callSource?: string;
  resultSource?: string;
  callArgs: unknown;
  result: unknown;
  isLatest?: boolean;
}) {
  const [open, setOpen] = useState(isLatest);

  // Latest: full box style (border + bg); older: left-strip style
  const isBoxStyle = isLatest;

  return (
    <div className={cn(
      "my-1",
      isBoxStyle
        ? "rounded-lg border-2 border-amber-400 dark:border-amber-500 bg-amber-50/30 dark:bg-amber-900/20 px-3 py-2"
        : "border-l-2 pl-3 py-0.5",
      !isBoxStyle && (open
        ? "border-l-zinc-400 dark:border-l-zinc-500"
        : "border-l-zinc-300 dark:border-l-zinc-600"
      )
    )}>
      {/* Summary row — click to toggle */}
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-1.5 text-left py-0.5 group"
      >
        {/* Chevron */}
        <span className={cn(
          "text-zinc-400 dark:text-zinc-500 transition-transform duration-150 flex-shrink-0 text-[10px]",
          open ? "rotate-90" : "rotate-0"
        )}>▶</span>
        {/* Tool name */}
        <span className="text-xs font-mono font-medium text-zinc-700 dark:text-zinc-300 truncate flex-1">{title}</span>
        {/* Source badges */}
        {callSource && (
          <span className="text-[10px] text-zinc-400 dark:text-zinc-500 flex-shrink-0">{callSource}</span>
        )}
        {callSource && resultSource && callSource !== resultSource && (
          <>
            <span className="text-zinc-300 dark:text-zinc-600 text-[10px]">→</span>
            <span className="text-[10px] text-zinc-400 dark:text-zinc-500 flex-shrink-0">{resultSource}</span>
          </>
        )}
      </button>
      {/* Expanded content */}
      {open && (
        <div className="mt-1 space-y-1">
          {/* CALL section */}
          <details>
            <summary className="list-none flex items-center gap-1.5 cursor-pointer py-0.5 group/call">
              {callSource && (
                <>
                  <span className="text-[10px] text-zinc-400 dark:text-zinc-500">{callSource}</span>
                  <span className="text-zinc-300 dark:text-zinc-600 text-[10px]">&middot;</span>
                </>
              )}
              <span className="text-[10px] uppercase tracking-wider font-semibold text-zinc-500 dark:text-zinc-400">Call</span>
              <span className="ml-auto text-zinc-400 dark:text-zinc-500 text-[10px] group-open/call:hidden">展开</span>
              <span className="ml-auto text-zinc-400 dark:text-zinc-500 text-[10px] hidden group-open/call:inline">收起</span>
            </summary>
            <div className={cn(scrollClasses, "mt-0.5 rounded-sm border border-zinc-100 dark:border-zinc-800")}>{renderContent(callArgs)}</div>
          </details>
          {/* OUTPUT section */}
          <details>
            <summary className="list-none flex items-center gap-1.5 cursor-pointer py-0.5 group/out">
              {resultSource && (
                <>
                  <span className="text-[10px] text-zinc-400 dark:text-zinc-500">{resultSource}</span>
                  <span className="text-zinc-300 dark:text-zinc-600 text-[10px]">&middot;</span>
                </>
              )}
              <span className="text-[10px] uppercase tracking-wider font-semibold text-emerald-600 dark:text-emerald-400">Output</span>
              <span className="ml-auto text-zinc-400 dark:text-zinc-500 text-[10px] group-open/out:hidden">展开</span>
              <span className="ml-auto text-zinc-400 dark:text-zinc-500 text-[10px] hidden group-open/out:inline">收起</span>
            </summary>
            <div className={cn(scrollClasses, "mt-0.5 rounded-sm border border-zinc-100 dark:border-zinc-800")}>{renderContent(result)}</div>
          </details>
        </div>
      )}
    </div>
  );
});

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

const StatusEvent = React.memo(function StatusEvent({ entry }: { entry: LogEntry }) {
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
      <div className="flex justify-center my-2">
        <div className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-green-100 dark:bg-green-900/40 border border-green-200 dark:border-green-800 max-w-[90%]">
          <span className="text-green-600 dark:text-green-400 text-base">✓</span>
          <span className="text-sm font-semibold text-green-800 dark:text-green-300 truncate">
            {String(entry.content) || "Done"}
          </span>
        </div>
      </div>
    );
  }
  if (entry.type === "error") {
    return (
      <div className="flex justify-center my-2">
        <div className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-red-100 dark:bg-red-900/40 border border-red-200 dark:border-red-800 max-w-[90%]">
          <span className="text-red-600 dark:text-red-400 text-base">✗</span>
          <span className="text-sm font-semibold text-red-800 dark:text-red-300 break-words">
            {String(entry.content)}
          </span>
        </div>
      </div>
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
      <div className="flex justify-center my-2">
        <div className="inline-flex items-center gap-2 px-4 py-2.5 rounded-lg bg-purple-50 dark:bg-purple-900/30 border border-purple-200 dark:border-purple-800 max-w-[90%]">
          <span className="text-purple-500 dark:text-purple-400 text-base flex-shrink-0">&#8635;</span>
          <div className="min-w-0">
            <div className="text-xs font-semibold text-purple-700 dark:text-purple-300">
              Replan triggered{c?.after_step != null ? ` after step ${c.after_step}` : ""}
            </div>
            {c?.reason && (
              <div className="text-[11px] text-purple-600 dark:text-purple-400 mt-0.5 truncate">{c.reason}</div>
            )}
          </div>
        </div>
      </div>
    );
  }
  if (entry.type === "plan_revised") {
    const c = entry.content as { old_step_count?: number; new_step_count?: number; replan_count?: number } | null;
    return (
      <div className="flex justify-center my-2">
        <div className="inline-flex items-center gap-2 px-4 py-2.5 rounded-lg bg-purple-50 dark:bg-purple-900/30 border border-purple-200 dark:border-purple-800 max-w-[90%]">
          <span className="text-purple-500 dark:text-purple-400 text-base flex-shrink-0">&#8635;</span>
          <div className="min-w-0">
            <div className="text-xs font-semibold text-purple-700 dark:text-purple-300">
              Plan revised: {c?.old_step_count ?? "?"} &rarr; {c?.new_step_count ?? "?"} steps
            </div>
            {c?.replan_count != null && (
              <div className="text-[11px] text-purple-600 dark:text-purple-400 mt-0.5">Revision #{c.replan_count}</div>
            )}
          </div>
        </div>
      </div>
    );
  }
  if (entry.type === "status_stages") {
    const c = entry.content as { total?: number; current?: number; intent?: string } | null;
    const pct = c?.total ? Math.round(((c?.current ?? 0) / c.total) * 100) : 0;
    return (
      <div className="flex justify-center my-2">
        <div className="w-[90%] max-w-md rounded-lg bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 overflow-hidden">
          <div className="flex items-center gap-2 px-3 py-2">
            <span className="text-blue-500 dark:text-blue-400 text-sm flex-shrink-0">▶</span>
            <span className="text-xs font-semibold text-blue-700 dark:text-blue-300">
              Step {c?.current ?? "?"}/{c?.total ?? "?"}
            </span>
            {c?.intent && (
              <span className="text-[11px] text-blue-600 dark:text-blue-400 truncate">{c.intent}</span>
            )}
          </div>
          {c?.total && c.total > 0 && (
            <div className="h-1 bg-blue-100 dark:bg-blue-900/40">
              <div
                className="h-full bg-blue-400 dark:bg-blue-500 transition-all duration-300"
                style={{ width: `${pct}%` }}
              />
            </div>
          )}
        </div>
      </div>
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
});

// ─── Tool payload parsers ───────────────────────────────────────────────────

function parseToolPayload(content: unknown): { id?: string; name: string; args?: unknown } | null {
  if (content == null) return null;
  if (typeof content === "string") {
    try {
      const parsed = JSON.parse(content) as { id?: string; name?: string; args?: unknown };
      return { id: parsed?.id, name: parsed?.name ?? "tool", args: parsed?.args };
    } catch {
      return { name: "tool", args: content };
    }
  }
  if (typeof content === "object") {
    const c = content as { id?: string; name?: string; args?: unknown };
    return { id: c?.id, name: c?.name ?? "tool", args: c?.args };
  }
  return null;
}

function parseToolResultPayload(content: unknown): { id?: string; name: string; result?: unknown } | null {
  if (content == null) return null;
  if (typeof content === "string") {
    try {
      const parsed = JSON.parse(content) as { id?: string; name?: string; result?: unknown };
      return { id: parsed?.id, name: parsed?.name ?? "result", result: parsed?.result };
    } catch {
      return { name: "result", result: content };
    }
  }
  if (typeof content === "object") {
    const c = content as { id?: string; name?: string; result?: unknown };
    return { id: c?.id, name: c?.name ?? "result", result: c?.result };
  }
  return null;
}

function formatPlannerText(text: string): string {
  // Normalize line endings
  const lines = text.replace(/\r\n/g, "\n").replace(/\r/g, "\n").split("\n");
  const out: string[] = [];

  for (let i = 0; i < lines.length; i++) {
    let line = lines[i];

    // [Plan Report] Section title: -> ## Section title
    const planReportMatch = line.match(/^\[Plan Report\]\s+([^:]+):\s*$/);
    if (planReportMatch) {
      // Add blank line before header (if previous line wasn't blank)
      if (out.length > 0 && out[out.length - 1] !== "") out.push("");
      out.push(`## ${planReportMatch[1].trim()}`);
      out.push("");
      continue;
    }

    // Summary: at line start -> ## Summary
    if (/^Summary:\s*$/.test(line)) {
      if (out.length > 0 && out[out.length - 1] !== "") out.push("");
      out.push("## Summary");
      out.push("");
      continue;
    }

    // Overall: Low/Medium/High -> bold
    line = line.replace(/^(Overall:\s*)(Low|Medium|High)/, "**$1$2**");

    // "Step N — Low — ..." or "Step N: Low — ..." -> list item
    line = line.replace(/^(Step \d+)\s*[—:]\s*/, "- **$1**: ");

    // Notes: at line start -> bold
    line = line.replace(/^(Notes?:)/, "**$1**");

    // → bullet points (indent as sub-list)
    line = line.replace(/^→\s+/, "  - ");

    // For non-list, non-blank lines: ensure paragraph separation
    // If current line is non-empty, non-list, and previous non-blank line was also non-list,
    // add a blank line between them so Markdown renders them as separate paragraphs
    const isListItem = line.startsWith("- ") || line.startsWith("  - ");
    const prevNonBlank = out.filter(l => l !== "").slice(-1)[0] ?? "";
    const prevIsListItem = prevNonBlank.startsWith("- ") || prevNonBlank.startsWith("  - ");

    if (line !== "" && !isListItem && !prevIsListItem && out.length > 0 && out[out.length - 1] !== "") {
      out.push("");
    }

    out.push(line);
  }

  return out.join("\n");
}

/** Render a planner JSON plan object as a rich structured React card */
function PlannerJsonCard({ obj }: { obj: Record<string, unknown> }): React.ReactElement {
  const strVal = (v: unknown): string => {
    if (typeof v === "string") return v;
    if (Array.isArray(v)) return v.map(strVal).join(", ");
    if (v !== null && typeof v === "object") return JSON.stringify(v, null, 2);
    return String(v ?? "");
  };

  const titleCase = (s: string) =>
    s.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());

  // ── Meta fields (top-level scalars) ──────────────────────────────────────
  const META_KEYS = new Set(["plan_id", "status", "strategy_name", "timeline_label", "execution_track"]);
  const metaEntries = Object.entries(obj).filter(
    ([k]) => META_KEYS.has(k) || (typeof obj[k] !== "object" && !Array.isArray(obj[k]))
  );

  // ── Execution steps: support both execution_graph (LLM schema) and execution_steps (internal) ──
  const steps = Array.isArray(obj.execution_graph)
    ? (obj.execution_graph as Record<string, unknown>[])
    : Array.isArray(obj.execution_steps)
    ? (obj.execution_steps as Record<string, unknown>[])
    : null;

  // ── Plan report ───────────────────────────────────────────────────────────
  const report =
    obj.plan_report !== null && typeof obj.plan_report === "object" && !Array.isArray(obj.plan_report)
      ? (obj.plan_report as Record<string, unknown>)
      : null;

  // ── Other top-level keys (not meta, not steps, not report) ───────────────
  // Note: typeof null === "object", so we must also exclude null values
  const otherEntries = Object.entries(obj).filter(
    ([k]) =>
      !META_KEYS.has(k) &&
      k !== "execution_steps" &&
      k !== "execution_graph" &&
      k !== "plan_report" &&
      obj[k] !== null &&
      obj[k] !== undefined &&
      typeof obj[k] === "object"
  );

  const intensityColor = (v: string) => {
    const l = v.toLowerCase();
    if (l === "high") return "text-red-600 dark:text-red-400";
    if (l === "medium") return "text-amber-600 dark:text-amber-400";
    if (l === "low") return "text-emerald-600 dark:text-emerald-400";
    return "text-zinc-600 dark:text-zinc-400";
  };

  const statusBadge = (v: string) => {
    const l = v.toLowerCase();
    const base = "inline-block px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide";
    if (l === "approved" || l === "active" || l === "complete")
      return <span className={cn(base, "bg-emerald-100 dark:bg-emerald-900/40 text-emerald-700 dark:text-emerald-300")}>{v}</span>;
    if (l === "pending" || l === "draft")
      return <span className={cn(base, "bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-300")}>{v}</span>;
    return <span className={cn(base, "bg-blue-100 dark:bg-blue-900/40 text-blue-700 dark:text-blue-300")}>{v}</span>;
  };

  return (
    <div className="space-y-3">
      {/* ── Meta section ── */}
      {metaEntries.length > 0 && (
        <div className="space-y-2">
          {metaEntries.map(([k, v]) => (
            <div key={k} className="flex flex-col gap-0.5">
              <span className="text-[9px] uppercase tracking-widest font-semibold text-blue-500 dark:text-blue-400">
                {titleCase(k)}
              </span>
              {k === "status" && typeof v === "string"
                ? statusBadge(v)
                : <span className="text-xs text-zinc-700 dark:text-zinc-300 font-medium">{strVal(v)}</span>
              }
            </div>
          ))}
        </div>
      )}

      {/* ── Execution Graph (Mermaid Diagram) ── */}
      {steps && steps.length > 0 && (
        <div>
          <div className="text-[9px] uppercase tracking-widest font-semibold text-blue-500 dark:text-blue-400 mb-1.5">
            Execution Graph
          </div>
          <ExecutionGraphRenderer steps={steps as Record<string, unknown>[]} />
        </div>
      )}

      {/* ── Execution Steps ── */}
      {steps && steps.length > 0 && (
        <div>
          <div className="text-[9px] uppercase tracking-widest font-semibold text-blue-500 dark:text-blue-400 mb-1.5">
            Execution Steps
          </div>
          <div className="space-y-2">
            {steps.map((step, i) => {
              const stepId = step.step_id ?? step.id ?? `Step ${i + 1}`;
              const goal = step.goal ?? step.description ?? step.name ?? "";
              const stepType = step.step_type ?? step.type ?? "";
              const intensity = step.compute_intensity ?? step.intensity ?? "";
              const fallback = step.fallback_strategy ?? step.fallback ?? "";
              const resources = step.resources_confirmation ?? step.resources ?? "";
              const otherStepKeys = Object.keys(step).filter(
                k => !["step_id", "id", "goal", "description", "name", "step_type", "type",
                       "compute_intensity", "intensity", "fallback_strategy", "fallback",
                       "resources_confirmation", "resources"].includes(k)
              );
              return (
                <div key={i} className="rounded-md border border-blue-200 dark:border-blue-800/60 bg-white/60 dark:bg-blue-950/20 px-3 py-2">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="flex-shrink-0 w-5 h-5 rounded-full bg-blue-100 dark:bg-blue-900/50 text-blue-700 dark:text-blue-300 text-[10px] font-bold flex items-center justify-center">
                      {i + 1}
                    </span>
                    <span className="text-xs font-semibold text-zinc-800 dark:text-zinc-200 flex-1">
                      {strVal(stepId)}
                    </span>
                    {stepType && (
                      <span className="text-[9px] px-1.5 py-0.5 rounded bg-blue-100 dark:bg-blue-900/40 text-blue-600 dark:text-blue-300 font-medium uppercase tracking-wide">
                        {strVal(stepType)}
                      </span>
                    )}
                  </div>
                  {goal && (
                    <p className="text-xs text-zinc-600 dark:text-zinc-400 mb-1 leading-relaxed">{strVal(goal)}</p>
                  )}
                  <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-[10px]">
                    {intensity && (
                      <span>
                        <span className="text-zinc-400 dark:text-zinc-500">Intensity: </span>
                        <span className={cn("font-medium", intensityColor(strVal(intensity)))}>{strVal(intensity)}</span>
                      </span>
                    )}
                    {fallback && (
                      <span>
                        <span className="text-zinc-400 dark:text-zinc-500">Fallback: </span>
                        <span className="text-zinc-600 dark:text-zinc-400">{strVal(fallback)}</span>
                      </span>
                    )}
                    {resources && (
                      <span>
                        <span className="text-zinc-400 dark:text-zinc-500">Resources: </span>
                        <span className="text-zinc-600 dark:text-zinc-400">{strVal(resources)}</span>
                      </span>
                    )}
                    {otherStepKeys.map(k => (
                      <span key={k}>
                        <span className="text-zinc-400 dark:text-zinc-500">{titleCase(k)}: </span>
                        <span className="text-zinc-600 dark:text-zinc-400">{strVal(step[k])}</span>
                      </span>
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* ── Plan Report ── */}
      {report && (
        <div>
          <div className="text-[9px] uppercase tracking-widest font-semibold text-blue-500 dark:text-blue-400 mb-1.5">
            Plan Report
          </div>
          <div className="space-y-1.5">
            {Object.entries(report).map(([k, v]) => (
              <div key={k}>
                <span className="text-[10px] font-semibold text-zinc-500 dark:text-zinc-400 uppercase tracking-wide">
                  {titleCase(k)}:{" "}
                </span>
                {Array.isArray(v) ? (
                  <ul className="mt-0.5 ml-3 space-y-0.5">
                    {(v as unknown[]).map((item, i) => (
                      <li key={i} className="text-xs text-zinc-600 dark:text-zinc-400 list-disc list-inside">
                        {/* Each risk/alternative item may itself be an object */}
                        {item !== null && typeof item === "object" && !Array.isArray(item)
                          ? <span className="space-x-1">
                              {Object.entries(item as Record<string, unknown>).map(([ik, iv]) => (
                                <span key={ik}><span className="text-zinc-400">{titleCase(ik)}:</span> {strVal(iv)}</span>
                              ))}
                            </span>
                          : strVal(item)
                        }
                      </li>
                    ))}
                  </ul>
                ) : v !== null && v !== undefined && typeof v === "object" && !Array.isArray(v) ? (
                  // Nested object (e.g. cost_assessment)
                  <div className="mt-0.5 ml-3 space-y-0.5">
                    {Object.entries(v as Record<string, unknown>).map(([ik, iv]) => (
                      <div key={ik} className="text-xs">
                        <span className="text-zinc-400 dark:text-zinc-500">{titleCase(ik)}: </span>
                        {Array.isArray(iv) ? (
                          <ul className="ml-2 mt-0.5 space-y-0.5">
                            {(iv as unknown[]).map((item, i) => (
                              <li key={i} className="text-zinc-600 dark:text-zinc-400 list-disc list-inside">
                                {item !== null && typeof item === "object" && !Array.isArray(item)
                                  ? Object.entries(item as Record<string, unknown>).map(([jk, jv]) => (
                                      <span key={jk} className="mr-2"><span className="text-zinc-400">{titleCase(jk)}:</span> {strVal(jv)}</span>
                                    ))
                                  : strVal(item)
                                }
                              </li>
                            ))}
                          </ul>
                        ) : (
                          <span className="text-zinc-700 dark:text-zinc-300">{strVal(iv)}</span>
                        )}
                      </div>
                    ))}
                  </div>
                ) : (
                  <span className="text-xs text-zinc-700 dark:text-zinc-300">{strVal(v)}</span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── Other top-level object keys ── */}
      {otherEntries.map(([k, v]) => (
        <div key={k}>
          <div className="text-[9px] uppercase tracking-widest font-semibold text-blue-500 dark:text-blue-400 mb-1">
            {titleCase(k)}
          </div>
          {Array.isArray(v) ? (
            <ul className="space-y-0.5 ml-1">
              {(v as unknown[]).map((item, i) => (
                <li key={i} className="text-xs text-zinc-600 dark:text-zinc-400 flex gap-1.5">
                  <span className="text-blue-400 mt-0.5">›</span>
                  <span>{strVal(item)}</span>
                </li>
              ))}
            </ul>
          ) : v !== null && v !== undefined && typeof v === "object" ? (
            <div className="text-xs text-zinc-600 dark:text-zinc-400 space-y-0.5">
              {Object.entries(v as Record<string, unknown>).map(([ik, iv]) => (
                <div key={ik} className="flex gap-1.5">
                  <span className="text-zinc-400 dark:text-zinc-500 flex-shrink-0">{titleCase(ik)}:</span>
                  <span className="text-zinc-700 dark:text-zinc-300">{strVal(iv)}</span>
                </div>
              ))}
            </div>
          ) : (
            <span className="text-xs text-zinc-600 dark:text-zinc-400">{strVal(v)}</span>
          )}
        </div>
      ))}
    </div>
  );
}

function renderEntry(entry: LogEntry, isPlannerMode = false, source?: string): React.ReactNode {
  if (entry.type === "planner_reply") {
    // planner_reply content is always a dict from _normalize_planner_thought:
    //   { message?: string, tag?: string, data?: object }
    // OR the raw dict if the LLM returned a JSON object directly.
    let messageText: string | null = null;
    let dataObj: Record<string, unknown> | null = null;
    let tagLabel: string | null = null;

    if (entry.content !== null && typeof entry.content === "object") {
      const c = entry.content as Record<string, unknown>;
      // Extract tag for header label
      if (typeof c.tag === "string" && c.tag.trim()) {
        tagLabel = c.tag.replace(/_/g, " ").replace(/\b\w/g, ch => ch.toUpperCase());
      }
      // Extract structured data
      if (c.data !== null && typeof c.data === "object" && !Array.isArray(c.data)) {
        dataObj = c.data as Record<string, unknown>;
      }
      // Extract message text — only show if there's no structured data to display.
      // When dataObj is set, the rich card already shows all info; suppress message
      // to avoid showing raw JSON strings alongside the card.
      if (dataObj === null && typeof c.message === "string" && c.message.trim()) {
        const msgTrimmed = c.message.trim();
        // Don't show message if it's a raw JSON string
        if (!msgTrimmed.startsWith("{") && !msgTrimmed.startsWith("[")) {
          messageText = msgTrimmed;
        }
      }
      // If no data and no message, treat the whole object as the plan JSON
      // (e.g. when LLM returned a dict directly and _normalize returned it as-is)
      if (dataObj === null && messageText === null) {
        // Check if it looks like a plan object (has plan_id, ready_to_plan, etc.)
        const hasKnownPlanKey = "plan_id" in c || "ready_to_plan" in c || "execution_graph" in c || "execution_steps" in c;
        if (hasKnownPlanKey) {
          dataObj = c;
        } else if (typeof c.message === "string") {
          messageText = c.message;
        } else {
          dataObj = c; // fallback: render whole object as structured card
        }
      }
    } else if (typeof entry.content === "string") {
      const trimmed = (entry.content as string).trim();
      if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
        try {
          const parsed = JSON.parse(trimmed);
          if (parsed !== null && typeof parsed === "object" && !Array.isArray(parsed)) {
            dataObj = parsed as Record<string, unknown>;
          }
        } catch { messageText = entry.content as string; }
      } else {
        messageText = entry.content as string;
      }
    }

    // Build rendered content
    const textNode = messageText
      ? renderMarkdown(formatPlannerText(messageText))
      : null;
    const dataNode = dataObj
      ? <PlannerJsonCard obj={dataObj} />
      : null;

    const headerLabel = tagLabel ?? "Planner";

    return (
      <div className="my-1 border-l-2 border-slate-400 dark:border-slate-500 pl-3 py-1.5 bg-slate-50/70 dark:bg-slate-800/30 rounded-r-md">
        <div className="flex items-center gap-1.5 mb-1.5">
          <span className="text-[10px] uppercase tracking-wider font-medium text-slate-500 dark:text-slate-400">
            {headerLabel}
          </span>
        </div>
        <div className={cn(
          "text-sm text-zinc-600 dark:text-zinc-400 break-words",
          "[&_.content-renderer_h2]:text-slate-700 [&_.content-renderer_h2]:dark:text-slate-300",
          "[&_.content-renderer_h2]:text-xs [&_.content-renderer_h2]:uppercase [&_.content-renderer_h2]:tracking-wide [&_.content-renderer_h2]:font-semibold",
          "[&_.content-renderer_h2]:mt-3 [&_.content-renderer_h2]:mb-1",
          "[&_.content-renderer_h2]:border-b [&_.content-renderer_h2]:border-slate-200 [&_.content-renderer_h2]:dark:border-slate-700 [&_.content-renderer_h2]:pb-0.5",
          "[&_.content-renderer_li]:text-zinc-600 [&_.content-renderer_li]:dark:text-zinc-400",
          "[&_.content-renderer_strong]:text-zinc-800 [&_.content-renderer_strong]:dark:text-zinc-200",
        )}>
          {textNode && <div className="mb-2">{textNode}</div>}
          {dataNode}
        </div>
      </div>
    );
  }
  if (entry.type === "tool_call") {
    const payload = parseToolPayload(entry.content);
    if (payload) {
      return <ToolCard title={payload.name} content={payload.args} isResult={false} sourceLabel={source} />;
    }
  }
  if (entry.type === "tool_result") {
    const payload = parseToolResultPayload(entry.content);
    if (payload) {
      return <ToolCard title={payload.name} content={payload.result} isResult={true} sourceLabel={source} />;
    }
  }
  if (entry.type === "execution_summary") {
    return (
      <div className="my-2 rounded-lg border border-emerald-300 dark:border-emerald-700 bg-emerald-50 dark:bg-emerald-900/30 p-4 shadow-sm">
        <div className="flex items-center gap-2 mb-2">
          <span className="text-emerald-600 dark:text-emerald-400 text-base">✓</span>
          <span className="text-[10px] uppercase tracking-wider font-bold text-emerald-700 dark:text-emerald-400">
            Summary
          </span>
        </div>
        <div className="text-sm font-medium text-zinc-800 dark:text-zinc-200">{renderContent(entry.content)}</div>
      </div>
    );
  }
  if (entry.type === "context_compaction") {
    const c = entry.content as {
      status?: string;
      tokens_before?: number;
      tokens_after?: number;
      tokens_saved?: number;
      compressed_turns?: number;
    } | null;
    const status = c?.status ?? "finished";
    if (status !== "finished") return null;
    const before = c?.tokens_before;
    const after = c?.tokens_after;
    const saved = c?.tokens_saved ?? (before != null && after != null ? before - after : undefined);
    const turns = c?.compressed_turns;
    const label = [
      "🗜 Context compacted",
      before != null && after != null ? `${before.toLocaleString()} → ${after.toLocaleString()} tokens` : null,
      saved != null ? `(−${saved.toLocaleString()})` : null,
      turns != null ? `· ${turns} turns compressed` : null,
    ].filter(Boolean).join(" ");
    return (
      <div className="flex justify-center my-1">
        <span className="inline-flex items-center gap-1.5 text-[11px] px-3 py-1 rounded-full font-medium bg-violet-50 text-violet-700 dark:bg-violet-900/30 dark:text-violet-300 max-w-[90%] truncate">
          {label}
        </span>
      </div>
    );
  }
  return renderContent(entry.content);
}

// ─── MessageBubble (memo) ───────────────────────────────────────────────────

const MessageBubble = React.memo(function MessageBubble({
  entry,
  isUser,
  isPlannerMode = false,
}: {
  entry: LogEntry;
  isUser: boolean;
  isPlannerMode?: boolean;
}) {
  const source = entry.source;
  const content = renderEntry(entry, isPlannerMode, source);
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

  // Thought events: left-border strip style, slate-blue tint, height follows content
  if (isThought) {
    return (
      <div className="flex w-full justify-start">
        <div className="max-w-[85%] border-l-2 border-slate-400 dark:border-slate-500 pl-3 py-1.5 bg-slate-50/70 dark:bg-slate-800/30 rounded-r-md">
          <details className="group">
            <summary className="list-none flex items-center gap-1.5 cursor-pointer text-xs font-medium text-slate-500 dark:text-slate-400">
              <span className="inline-block transition group-open:rotate-90 text-[10px]">&#9654;</span>
              {source} &middot; Thinking
            </summary>
            <div className="mt-1.5 text-sm text-zinc-600 dark:text-zinc-400 break-words whitespace-pre-wrap">
              {mainContent}
            </div>
          </details>
        </div>
      </div>
    );
  }

  // tool_call/tool_result/planner_reply/execution_summary/context_compaction render with their own card styling.
  // Skip the outer bubble wrapper to avoid "card inside card" double-boxing.
  if (entry.type === "tool_call" || entry.type === "tool_result" || entry.type === "planner_reply" || entry.type === "execution_summary" || entry.type === "context_compaction") {
    return (
      <div className="flex w-full justify-start">
        <div className="w-full max-w-[92%]">
          {mainContent}
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
});

// ─── ChatPanel ──────────────────────────────────────────────────────────────

/**
 * Streaming window: fixed-height area with top-fade mask.
 * New tokens scroll in from the bottom; the top edge fades out smoothly.
 * Once streaming ends the parent clears streamingContent and the final
 * `thought` event renders as a normal message bubble.
 */
function StreamingBubble({
  source,
  content,
  isStreaming,
}: {
  source: string;
  content: string;
  isStreaming?: boolean;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  // Track whether user has scrolled away from the bottom
  const isNearBottomRef = useRef(true);

  // Listen for user scroll: if they scroll up, stop auto-scrolling
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const onScroll = () => {
      const { scrollTop, scrollHeight, clientHeight } = el;
      isNearBottomRef.current = scrollTop + clientHeight >= scrollHeight - 32;
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, []);

  // Auto-scroll to bottom only when user hasn't scrolled up
  useEffect(() => {
    const el = scrollRef.current;
    if (el && isNearBottomRef.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [content]);

  if (!content && !isStreaming) return null;

  // Backward compatibility: if isStreaming is undefined, default to showing pulse (old behavior)
  const showPulse = isStreaming ?? true;

  return (
    <div className="flex w-full justify-start">
      <div className="max-w-[85%] w-full border-l-2 border-slate-400 dark:border-slate-500 pl-3 bg-slate-50/70 dark:bg-slate-800/30 rounded-r-md">
        {/* Source label + pulse dot */}
        <div className="flex items-center gap-1.5 mb-0.5 pt-1.5">
          <span className="text-[10px] font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">
            {source}
          </span>
          {showPulse && (
            <span className="inline-block w-1 h-1 rounded-full bg-slate-400 dark:bg-slate-500 animate-pulse" />
          )}
        </div>
        {/* Fixed-height scroll window with top-fade mask */}
        <div
          ref={scrollRef}
          className="h-32 overflow-y-auto scrollbar-thin-slate pb-1.5"
          style={{
            maskImage: "linear-gradient(to bottom, transparent 0%, black 30%, black 100%)",
            WebkitMaskImage: "linear-gradient(to bottom, transparent 0%, black 30%, black 100%)",
          }}
        >
          <p className="text-sm text-zinc-600 dark:text-zinc-400 whitespace-pre-wrap break-words pt-20 pb-1">
            {content}
          </p>
        </div>
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
  streamingContent = "",
  streamingSource = "MatMaster",
  isStreaming = false,
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
  streamingContent?: string;
  streamingSource?: string;
  isStreaming?: boolean;
}) {
  // ── useMemo: derived filtered list ──
  const filtered = useMemo(
    () => {
      const mapped = entries
        .map((e, index) => ({ entry: e, index }))
        .filter(
          ({ entry: e }) =>
            e.type !== "log_line" &&
            e.type !== "status" &&
            !isEnvRelatedEntry(e) &&
            !isEmptyThought(e)
        );

      // Deduplicate consecutive planner_reply entries
      // Skip plain-text versions that follow structured versions (same semantic content)
      const deduped: typeof mapped = [];
      let lastPlannerReplyContent: unknown = null;
      let lastPlannerReplyHasData = false;

      for (const item of mapped) {
        if (item.entry.type === "planner_reply") {
          const content = item.entry.content;
          const contentStr = JSON.stringify(content);

          // Check if this is a plain text version following a structured version
          if (
            lastPlannerReplyHasData &&
            typeof content === "string" &&
            lastPlannerReplyContent !== null
          ) {
            // Skip plain text if it looks like a text summary of the previous structured data
            // (e.g., "Ready to plan. The task is clear..." following {ready_to_plan: true, ...})
            const textLower = content.toLowerCase();
            if (
              textLower.includes("ready to plan") ||
              textLower.includes("ready") ||
              textLower.includes("task is clear")
            ) {
              continue;
            }
          }

          // Check for exact duplicates
          if (contentStr === JSON.stringify(lastPlannerReplyContent)) {
            continue;
          }

          lastPlannerReplyContent = content;
          lastPlannerReplyHasData = typeof content === "object" && content !== null;
        } else {
          lastPlannerReplyContent = null;
          lastPlannerReplyHasData = false;
        }
        deduped.push(item);
      }
      return deduped;
    },
    [entries]
  );

  // ── useMemo: current step index range for planner mode highlight ──
  const currentStepIndices = useMemo(() => {
    if (mode !== "planner") return new Set<number>();
    // Find the last status_stages event index in entries
    let lastStageIdx = -1;
    for (let i = entries.length - 1; i >= 0; i--) {
      if (entries[i].type === "status_stages") {
        lastStageIdx = i;
        break;
      }
    }
    if (lastStageIdx < 0) return new Set<number>();
    // All entries after the last status_stages are in the "current step"
    const indices = new Set<number>();
    for (let i = lastStageIdx + 1; i < entries.length; i++) {
      indices.add(i);
    }
    return indices;
  }, [entries, mode]);

  const isRunning = running && currentSessionId === runningSessionId;
  const canSend = status === "connected" && !isRunning;
  const [highlightIndex, setHighlightIndex] = useState<number | null>(null);
  const highlightTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const composerTextareaRef = useRef<HTMLTextAreaElement | null>(null);

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

  // ── Smart auto-scroll with requestAnimationFrame ──
  const NEAR_BOTTOM_THRESHOLD_PX = 50;
  const isNearBottomRef = useRef(true);
  const rafRef = useRef<number | null>(null);

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
      // Cancel any pending rAF to avoid stacking
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current);
      }
      rafRef.current = requestAnimationFrame(() => {
        if (scrollRef.current) {
          scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
        }
        rafRef.current = null;
      });
    }
  }, [filtered.length, streamingContent, scrollRef]);

  // Cleanup rAF on unmount
  useEffect(() => {
    return () => {
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current);
      }
    };
  }, []);

  useEffect(() => {
    if (jumpToLogIndex === null || jumpToLogIndex === undefined) return;
    const target = document.getElementById(`chat-log-${jumpToLogIndex}`);
    if (target) {
      target.scrollIntoView({ behavior: "smooth", block: "center" });
      setHighlightIndex(jumpToLogIndex);

      // Clear any existing timeout before setting a new one
      if (highlightTimeoutRef.current) {
        clearTimeout(highlightTimeoutRef.current);
      }

      // Set new timeout to clear highlight after 1800ms
      highlightTimeoutRef.current = setTimeout(() => {
        setHighlightIndex(null);
        highlightTimeoutRef.current = null;
      }, 1800);
    }
    onJumpHandled?.();

    // Cleanup: clear timeout if component unmounts or effect re-runs
    return () => {
      if (highlightTimeoutRef.current) {
        clearTimeout(highlightTimeoutRef.current);
      }
    };
  }, [jumpToLogIndex, onJumpHandled]);

  useEffect(() => {
    const textarea = composerTextareaRef.current;
    if (!textarea) return;

    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, 120)}px`;
  }, [input]);

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
          ) : (() => {
            // Two-pass pairing: tool_call + tool_result may not be consecutive and may arrive
            // out of order (concurrent tool calls). Strategy:
            //   Primary:  match by call id (content.id field) — exact, handles any ordering
            //   Fallback: FIFO by tool name — for entries without id
            type RenderItem =
              | { kind: "single"; entry: LogEntry; index: number; i: number }
              | { kind: "paired"; callEntry: LogEntry; resultEntry: LogEntry; callIndex: number; resultIndex: number; i: number };

            // Pass 1: index all tool_result entries
            //   - by id (if present): resultById map
            //   - by name (fallback): resultQueues FIFO map
            const resultById = new Map<string, { entry: LogEntry; index: number; filteredIdx: number }>();
            const resultQueues = new Map<string, Array<{ entry: LogEntry; index: number; filteredIdx: number }>>();
            for (let i = 0; i < filtered.length; i++) {
              const { entry, index } = filtered[i];
              if (entry.type === "tool_result") {
                const payload = parseToolResultPayload(entry.content);
                if (payload) {
                  if (payload.id) {
                    // id-keyed: exact match
                    resultById.set(payload.id, { entry, index, filteredIdx: i });
                  } else {
                    // no id: fallback FIFO by name
                    const name = payload.name;
                    if (!resultQueues.has(name)) resultQueues.set(name, []);
                    resultQueues.get(name)!.push({ entry, index, filteredIdx: i });
                  }
                }
              }
            }
            // Track which filteredIdx positions have been consumed as paired results
            const consumedResultIndices = new Set<number>();

            // Pass 2: build render items
            const items: RenderItem[] = [];
            for (let i = 0; i < filtered.length; i++) {
              const { entry: cur, index: curIdx } = filtered[i];
              // Skip tool_result entries that were consumed by a tool_call pair
              if (cur.type === "tool_result" && consumedResultIndices.has(i)) {
                continue;
              }
              if (cur.type === "tool_call") {
                const callPayload = parseToolPayload(cur.content);
                if (callPayload) {
                  let matched: { entry: LogEntry; index: number; filteredIdx: number } | undefined;
                  if (callPayload.id) {
                    // Primary: match by id
                    matched = resultById.get(callPayload.id);
                    if (matched) resultById.delete(callPayload.id);
                  }
                  if (!matched) {
                    // Fallback: FIFO by name
                    const queue = resultQueues.get(callPayload.name);
                    if (queue && queue.length > 0) matched = queue.shift();
                  }
                  if (matched) {
                    consumedResultIndices.add(matched.filteredIdx);
                    items.push({ kind: "paired", callEntry: cur, resultEntry: matched.entry, callIndex: curIdx, resultIndex: matched.index, i });
                    continue;
                  }
                }
              }
              items.push({ kind: "single", entry: cur, index: curIdx, i });
            }
            return items.map((item) => {
              if (item.kind === "paired") {
                const callPayload = parseToolPayload(item.callEntry.content)!;
                const resultPayload = parseToolResultPayload(item.resultEntry.content)!;
                const isHighlighted = highlightIndex === item.callIndex || highlightIndex === item.resultIndex;
                const isCurrentStep = !isHighlighted && (currentStepIndices.has(item.callIndex) || currentStepIndices.has(item.resultIndex));
                return (
                  <div
                    key={item.callEntry.msg_id ?? `pair-${item.i}`}
                    id={`chat-log-${item.callIndex}`}
                    className={cn(
                      "rounded-xl transition-colors duration-500",
                      isHighlighted && "bg-amber-100/60 dark:bg-amber-500/20"
                    )}
                  >
                    {/* Hidden anchor for result index so WorkspacePanel jump-to-result works */}
                    <span id={`chat-log-${item.resultIndex}`} aria-hidden="true" />
                    <div className="flex w-full justify-start">
                      <div className="w-full max-w-[92%]">
                        <PairedToolCard
                          title={callPayload.name}
                          callSource={item.callEntry.source}
                          resultSource={item.resultEntry.source}
                          callArgs={callPayload.args}
                          result={resultPayload.result}
                          isLatest={item.i === items.length - 1}
                        />
                      </div>
                    </div>
                  </div>
                );
              }
              const { entry: log, index, i } = item;
              return (
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
                      isPlannerMode={mode === "planner"}
                    />
                  )}
                </div>
              );
            });
          })()}
          {/* Streaming bubble: live LLM token output.
              Show as soon as llm_stream_start arrives (isStreaming=true),
              even before the first token (streamingContent may still be ""). */}
          {(isStreaming || streamingContent) && (
            <StreamingBubble
              source={streamingSource}
              content={streamingContent}
              isStreaming={isStreaming}
            />
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
                  ref={composerTextareaRef}
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
