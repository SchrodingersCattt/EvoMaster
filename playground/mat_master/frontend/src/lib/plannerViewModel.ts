/**
 * plannerViewModel.ts
 *
 * Transforms a flat LogEntry[] event stream into a hierarchical planner tree:
 *   Phase[] → Revision[] → Step[] → events (tool_call, tool_result, thought, etc.)
 *
 * Used by PlannerOutlinePanel to render a navigable outline.
 */

import type { LogEntry } from "../components/MatMasterView";

// ─── Types ──────────────────────────────────────────────────────────────────

export interface PlannerStep {
  /** 1-based step number within the revision */
  stepNumber: number;
  /** Step intent / description from status_stages */
  intent: string;
  /** Index of the status_stages entry in the original logs array */
  logIndex: number;
  /** Child events belonging to this step (tool_call, tool_result, thought, etc.) */
  events: PlannerEvent[];
  /** Whether this step is the currently executing one */
  isCurrent: boolean;
}

export interface PlannerEvent {
  type: string;
  source: string;
  /** Short label for display */
  label: string;
  /** Index in the original logs array */
  logIndex: number;
  /** Whether the event indicates success (for tool_result) */
  ok?: boolean;
}

export interface PlannerRevision {
  /** 0-based revision index (0 = original plan, 1+ = replans) */
  revisionIndex: number;
  /** Reason for replan (null for original plan) */
  replanReason: string | null;
  /** Steps in this revision */
  steps: PlannerStep[];
  /** Total steps declared in this revision */
  totalSteps: number;
  /** Index of the plan_revised entry in logs (null for original) */
  logIndex: number | null;
}

export interface PlannerPhase {
  name: string;
  label: string;
  logIndex: number;
}

export interface PlannerViewModel {
  phases: PlannerPhase[];
  revisions: PlannerRevision[];
  currentPhase: string;
  currentStep: number | null;
  totalSteps: number | null;
}

// ─── Phase label mapping ────────────────────────────────────────────────────

const PHASE_LABELS: Record<string, string> = {
  init: "Init",
  pre_check: "Pre-check",
  planning: "Planning",
  preflight: "Pre-flight",
  executing: "Executing",
  replanning: "Replanning",
  completed: "Completed",
  failed: "Failed",
  aborted: "Aborted",
};

// ─── Builder ────────────────────────────────────────────────────────────────

export function buildPlannerViewModel(entries: LogEntry[]): PlannerViewModel {
  const phases: PlannerPhase[] = [];
  const revisions: PlannerRevision[] = [];
  let currentPhase = "";
  let currentStepNum: number | null = null;
  let totalSteps: number | null = null;

  // Start with revision 0 (original plan)
  let currentRevision: PlannerRevision = {
    revisionIndex: 0,
    replanReason: null,
    steps: [],
    totalSteps: 0,
    logIndex: null,
  };
  revisions.push(currentRevision);

  // Track current step within revision
  let currentStep: PlannerStep | null = null;

  for (let i = 0; i < entries.length; i++) {
    const e = entries[i];

    // ── Phase changes ──
    if (e.type === "phase_change") {
      const c = e.content as { from?: string; to?: string } | null;
      const to = c?.to ?? "";
      currentPhase = to;
      phases.push({
        name: to,
        label: PHASE_LABELS[to] ?? to,
        logIndex: i,
      });
      continue;
    }

    // ── Plan revised → new revision ──
    if (e.type === "plan_revised") {
      const c = e.content as {
        old_step_count?: number;
        new_step_count?: number;
        replan_count?: number;
      } | null;

      // Finalize current step
      currentStep = null;

      currentRevision = {
        revisionIndex: revisions.length,
        replanReason: null,
        steps: [],
        totalSteps: c?.new_step_count ?? 0,
        logIndex: i,
      };
      revisions.push(currentRevision);
      continue;
    }

    // ── Replan triggered → attach reason to latest revision ──
    if (e.type === "replan_triggered") {
      const c = e.content as { reason?: string; after_step?: number } | null;
      // The next plan_revised will create a new revision;
      // for now, store the reason on the current revision if it's the latest
      if (revisions.length > 0) {
        const latest = revisions[revisions.length - 1];
        if (latest.replanReason === null) {
          latest.replanReason = c?.reason ?? "unknown";
        }
      }
      continue;
    }

    // ── Status stages → new step (start) or step done ──
    if (e.type === "status_stages") {
      const c = e.content as {
        total?: number;
        current?: number;
        step_id?: number;
        intent?: string;
        status?: string;
      } | null;

      // status:'done' — step completed; clear currentStep so the outline
      // shows no active step until the next step's status_stages arrives.
      // This fixes the "Step X/Y stuck" issue when step N finishes but
      // step N+1 hasn't started yet.
      if (c?.status === "done") {
        // Find the matching step and mark it as no longer current
        const doneId = c?.step_id ?? c?.current;
        if (doneId !== undefined) {
          for (const rev of revisions) {
            for (const s of rev.steps) {
              if (s.stepNumber === doneId) {
                s.isCurrent = false;
              }
            }
          }
        }
        // Clear the active step pointer so Step X/Y badge disappears
        // between steps (avoids stale display)
        currentStep = null;
        currentStepNum = null;
        continue;
      }

      const stepNum = c?.current ?? (currentRevision.steps.length + 1);
      totalSteps = c?.total ?? totalSteps;
      currentStepNum = stepNum;

      if (totalSteps !== null) {
        currentRevision.totalSteps = totalSteps;
      }

      currentStep = {
        stepNumber: stepNum,
        intent: c?.intent ?? `Step ${stepNum}`,
        logIndex: i,
        events: [],
        isCurrent: true,
      };

      // Mark all previous steps as not current
      for (const rev of revisions) {
        for (const s of rev.steps) {
          s.isCurrent = false;
        }
      }

      currentRevision.steps.push(currentStep);
      continue;
    }

    // ── Tool calls / results / thoughts → attach to current step ──
    if (
      e.type === "tool_call" ||
      e.type === "tool_result" ||
      e.type === "thought" ||
      e.type === "execution_summary"
    ) {
      const evt: PlannerEvent = {
        type: e.type,
        source: e.source,
        label: getEventLabel(e),
        logIndex: i,
      };

      if (e.type === "tool_result") {
        evt.ok = inferToolOk(e);
      }

      if (currentStep) {
        currentStep.events.push(evt);
      }
      // If no current step yet (events before first status_stages), skip
      continue;
    }
  }

  return {
    phases,
    revisions,
    currentPhase,
    currentStep: currentStepNum,
    totalSteps,
  };
}

// ─── Helpers ────────────────────────────────────────────────────────────────

function getEventLabel(entry: LogEntry): string {
  if (entry.type === "tool_call") {
    const c = entry.content as { name?: string } | null;
    if (c?.name) return `Call: ${c.name}`;
    if (typeof entry.content === "string") {
      try {
        const p = JSON.parse(entry.content) as { name?: string };
        return `Call: ${p.name ?? "tool"}`;
      } catch {
        return "Call: tool";
      }
    }
    return "Call: tool";
  }
  if (entry.type === "tool_result") {
    const c = entry.content as { name?: string } | null;
    if (c?.name) return `Result: ${c.name}`;
    if (typeof entry.content === "string") {
      try {
        const p = JSON.parse(entry.content) as { name?: string };
        return `Result: ${p.name ?? "result"}`;
      } catch {
        return "Result";
      }
    }
    return "Result";
  }
  if (entry.type === "thought") {
    return "Thinking";
  }
  if (entry.type === "execution_summary") {
    return "Summary";
  }
  return entry.type;
}

function inferToolOk(entry: LogEntry): boolean {
  if (entry.type !== "tool_result" || !entry.content || typeof entry.content !== "object")
    return true;
  const c = entry.content as { result?: string };
  const r = typeof c.result === "string" ? c.result : "";
  if (/error|failed|exception|exit code: [1-9]|non-zero exit/i.test(r)) return false;
  return true;
}
