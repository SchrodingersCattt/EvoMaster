"use client";

import React, { useMemo } from "react";
import type { LogEntry } from "./MatMasterView";
import {
  buildPlannerViewModel,
  type PlannerRevision,
  type PlannerStep,
  type PlannerEvent,
} from "@/lib/plannerViewModel";
import { cn } from "@/lib/utils";
import {
  ChevronDownIcon,
  ChevronRightIcon,
  CircleDotIcon,
  CircleCheckIcon,
  CircleXIcon,
  RefreshCwIcon,
} from "./icons";

// ─── Step item ──────────────────────────────────────────────────────────────

function StepItem({
  step,
  onJump,
}: {
  step: PlannerStep;
  onJump: (index: number) => void;
}) {
  const [expanded, setExpanded] = React.useState(step.isCurrent);

  const hasError = step.events.some((e) => e.type === "tool_result" && e.ok === false);
  const hasSummary = step.events.some((e) => e.type === "execution_summary");

  return (
    <li className="relative">
      {/* Step header */}
      <button
        type="button"
        onClick={() => {
          if (step.events.length > 0) {
            setExpanded((v) => !v);
          } else {
            onJump(step.logIndex);
          }
        }}
        className={cn(
          "w-full flex items-center gap-2 px-2 py-1.5 text-left text-xs rounded-md transition-colors",
          step.isCurrent
            ? "bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-400 font-semibold"
            : "text-zinc-600 dark:text-zinc-400 hover:bg-zinc-50 dark:hover:bg-zinc-800/50"
        )}
      >
        {/* Expand/collapse icon */}
        {step.events.length > 0 ? (
          expanded ? (
            <ChevronDownIcon size={12} className="shrink-0" />
          ) : (
            <ChevronRightIcon size={12} className="shrink-0" />
          )
        ) : (
          <span className="w-3" />
        )}

        {/* Status icon */}
        {step.isCurrent ? (
          <CircleDotIcon size={12} className="shrink-0 text-blue-500 animate-pulse" />
        ) : hasError ? (
          <CircleXIcon size={12} className="shrink-0 text-red-500" />
        ) : hasSummary ? (
          <CircleCheckIcon size={12} className="shrink-0 text-emerald-500" />
        ) : (
          <CircleCheckIcon size={12} className="shrink-0 text-zinc-400" />
        )}

        {/* Step label */}
        <span className="truncate flex-1">
          <span className="font-mono text-[10px] mr-1">#{step.stepNumber}</span>
          {step.intent}
        </span>
      </button>

      {/* Step events (collapsed by default for non-current) */}
      {expanded && step.events.length > 0 && (
        <ul className="ml-5 pl-2 border-l border-zinc-200 dark:border-zinc-700 space-y-0.5 py-1">
          {step.events.map((evt, j) => (
            <EventItem key={j} event={evt} onJump={onJump} />
          ))}
        </ul>
      )}
    </li>
  );
}

// ─── Event item ─────────────────────────────────────────────────────────────

function EventItem({
  event,
  onJump,
}: {
  event: PlannerEvent;
  onJump: (index: number) => void;
}) {
  const isToolResult = event.type === "tool_result";
  const isThought = event.type === "thought";

  return (
    <li>
      <button
        type="button"
        onClick={() => onJump(event.logIndex)}
        className={cn(
          "w-full flex items-center gap-1.5 px-1.5 py-0.5 text-left text-[11px] rounded transition-colors",
          "hover:bg-zinc-100 dark:hover:bg-zinc-800/50",
          isToolResult && event.ok === false
            ? "text-red-600 dark:text-red-400"
            : isThought
              ? "text-violet-600 dark:text-violet-400"
              : "text-zinc-500 dark:text-zinc-400"
        )}
        title="Jump to log"
      >
        {isToolResult ? (
          event.ok ? (
            <CircleCheckIcon size={10} className="shrink-0 text-emerald-500" />
          ) : (
            <CircleXIcon size={10} className="shrink-0 text-red-500" />
          )
        ) : isThought ? (
          <span className="shrink-0 text-[8px]">💭</span>
        ) : (
          <CircleDotIcon size={10} className="shrink-0 text-zinc-400" />
        )}
        <span className="truncate">{event.label}</span>
      </button>
    </li>
  );
}

// ─── Revision section ───────────────────────────────────────────────────────

function RevisionSection({
  revision,
  onJump,
}: {
  revision: PlannerRevision;
  onJump: (index: number) => void;
}) {
  const [expanded, setExpanded] = React.useState(true);
  const isOriginal = revision.revisionIndex === 0;

  return (
    <div className="space-y-1">
      {/* Revision header */}
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className={cn(
          "w-full flex items-center gap-2 px-2 py-1.5 text-left text-xs font-medium rounded-md transition-colors",
          isOriginal
            ? "text-zinc-700 dark:text-zinc-300 hover:bg-zinc-50 dark:hover:bg-zinc-800/50"
            : "text-purple-700 dark:text-purple-400 hover:bg-purple-50 dark:hover:bg-purple-900/20"
        )}
      >
        {expanded ? (
          <ChevronDownIcon size={14} className="shrink-0" />
        ) : (
          <ChevronRightIcon size={14} className="shrink-0" />
        )}
        {!isOriginal && <RefreshCwIcon size={12} className="shrink-0" />}
        <span className="truncate flex-1">
          {isOriginal
            ? `Plan (${revision.totalSteps} steps)`
            : `Replan #${revision.revisionIndex} (${revision.totalSteps} steps)`}
        </span>
      </button>

      {/* Replan reason */}
      {!isOriginal && revision.replanReason && expanded && (
        <div className="ml-6 text-[10px] text-purple-600 dark:text-purple-400 italic truncate px-1">
          {revision.replanReason}
        </div>
      )}

      {/* Steps */}
      {expanded && (
        <ul className="space-y-0.5 ml-2">
          {revision.steps.map((step) => (
            <StepItem key={step.stepNumber} step={step} onJump={onJump} />
          ))}
          {revision.steps.length === 0 && (
            <li className="text-[11px] text-zinc-400 dark:text-zinc-500 italic px-2 py-1">
              No steps yet…
            </li>
          )}
        </ul>
      )}
    </div>
  );
}

// ─── Main Panel ─────────────────────────────────────────────────────────────

export default function PlannerOutlinePanel({
  logs,
  onJumpToLogIndex,
}: {
  logs: LogEntry[];
  onJumpToLogIndex?: (index: number) => void;
}) {
  const viewModel = useMemo(() => buildPlannerViewModel(logs), [logs]);

  const handleJump = (index: number) => {
    onJumpToLogIndex?.(index);
  };

  return (
    <div className="p-2 space-y-2 text-xs">
      {/* Current phase badge */}
      {viewModel.currentPhase && (
        <div className="flex items-center gap-2 px-2 py-1">
          <span className="text-zinc-500 dark:text-zinc-400 font-medium">Phase</span>
          <span
            className={cn(
              "inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-semibold",
              viewModel.currentPhase === "executing"
                ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-400"
                : viewModel.currentPhase === "planning"
                  ? "bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-400"
                  : viewModel.currentPhase === "replanning"
                    ? "bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-400"
                    : viewModel.currentPhase === "completed"
                      ? "bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-400"
                      : viewModel.currentPhase === "failed"
                        ? "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-400"
                        : "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400"
            )}
          >
            {viewModel.currentPhase}
          </span>
          {viewModel.currentStep !== null && viewModel.totalSteps !== null && (
            <span className="text-zinc-500 dark:text-zinc-400 text-[10px]">
              Step {viewModel.currentStep}/{viewModel.totalSteps}
            </span>
          )}
        </div>
      )}

      {/* Revisions tree */}
      {viewModel.revisions.map((rev) => (
        <RevisionSection key={rev.revisionIndex} revision={rev} onJump={handleJump} />
      ))}

      {/* Empty state */}
      {viewModel.revisions.every((r) => r.steps.length === 0) && !viewModel.currentPhase && (
        <p className="text-zinc-500 dark:text-zinc-400 italic px-2 py-2">
          Waiting for planner…
        </p>
      )}
    </div>
  );
}
