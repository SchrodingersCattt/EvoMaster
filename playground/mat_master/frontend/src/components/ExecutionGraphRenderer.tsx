"use client";

import React, { useEffect, useRef, useState, useId, useCallback } from "react";
import { cn } from "@/lib/utils";

interface ExecutionGraphRendererProps {
  steps: Record<string, unknown>[];
  className?: string;
}

/**
 * Safely extract a value from a step object
 */
function getVal(step: Record<string, unknown>, ...keys: string[]): unknown {
  for (const key of keys) {
    if (key in step && step[key] !== undefined && step[key] !== null) {
      return step[key];
    }
  }
  return undefined;
}

/**
 * Escape special characters in Mermaid node labels
 */
function escapeMermaidLabel(text: string): string {
  return text
    .replace(/"/g, "#quot;")
    .replace(/</g, "#lt;")
    .replace(/>/g, "#gt;")
    .replace(/\n/g, " ")
    .replace(/\r/g, "")
    .substring(0, 55);
}

/**
 * Converts execution_graph steps to Mermaid flowchart syntax.
 * Handles:
 * - depends_on: sequential dependencies
 * - conditional_branch: if_success / if_fail branches
 * - fallback_strategy: fallback paths
 * - compute_intensity: node styling
 */
function generateMermaidDiagram(steps: Record<string, unknown>[]): string {
  if (!steps || steps.length === 0) {
    return "graph TD\n  A[No steps]";
  }

  const lines: string[] = ["graph TD"];

  // Define node styles based on intensity and status
  const getNodeStyle = (step: Record<string, unknown>): string => {
    const status = String(getVal(step, "status") || "pending").toLowerCase();
    if (status === "completed") return ":::completed";
    if (status === "failed") return ":::failed";

    const intensity = String(getVal(step, "compute_intensity", "intensity") || "MEDIUM").toUpperCase();
    if (intensity === "HIGH") return ":::high";
    if (intensity === "LOW") return ":::low";
    return ":::medium";
  };

  // Create node definitions
  steps.forEach((step) => {
    const stepId = getVal(step, "step_id", "id");
    const nodeId = `S${stepId}`;
    const goal = escapeMermaidLabel(String(getVal(step, "goal", "description", "name") || ""));
    const label = goal ? `${stepId}: ${goal}` : `Step ${stepId}`;
    const style = getNodeStyle(step);
    lines.push(`  ${nodeId}["${label}"]${style}`);
  });

  // Add edges for dependencies
  const addedEdges = new Set<string>();

  steps.forEach((step) => {
    const stepId = getVal(step, "step_id", "id");
    const nodeId = `S${stepId}`;

    // Handle depends_on (sequential dependencies)
    const dependsOn = getVal(step, "depends_on");
    if (Array.isArray(dependsOn)) {
      dependsOn.forEach((depId) => {
        const depNodeId = `S${depId}`;
        const edgeKey = `${depNodeId}->${nodeId}`;
        if (!addedEdges.has(edgeKey)) {
          lines.push(`  ${depNodeId} --> ${nodeId}`);
          addedEdges.add(edgeKey);
        }
      });
    }

    // Handle conditional_branch (if_success / if_fail)
    const conditionalBranch = getVal(step, "conditional_branch");
    if (conditionalBranch && typeof conditionalBranch === "object") {
      const branch = conditionalBranch as Record<string, unknown>;
      const ifSuccess = branch.if_success;
      const ifFail = branch.if_fail;

      if (ifSuccess !== undefined && ifSuccess !== null) {
        const successNodeId = `S${ifSuccess}`;
        const edgeKey = `${nodeId}-success->${successNodeId}`;
        if (!addedEdges.has(edgeKey)) {
          lines.push(`  ${nodeId} -->|Success| ${successNodeId}`);
          addedEdges.add(edgeKey);
        }
      }

      if (ifFail !== undefined && ifFail !== null) {
        const failNodeId = `S${ifFail}`;
        const edgeKey = `${nodeId}-fail->${failNodeId}`;
        if (!addedEdges.has(edgeKey)) {
          lines.push(`  ${nodeId} -->|Fail| ${failNodeId}`);
          addedEdges.add(edgeKey);
        }
      }
    }

    // Handle fallback_strategy — only if non-trivial
    const fallbackStrategy = getVal(step, "fallback_strategy", "fallback");
    const fallbackStr = String(fallbackStrategy || "").trim();
    if (fallbackStr && fallbackStr.toLowerCase() !== "none" && fallbackStr.length > 3) {
      const fallbackNodeId = `${nodeId}FB`;
      const shortFallback = escapeMermaidLabel(fallbackStr);
      lines.push(`  ${fallbackNodeId}["Fallback: ${shortFallback}"]:::fallback`);
      lines.push(`  ${nodeId} -.->|fallback| ${fallbackNodeId}`);
    }
  });

  // Add style definitions
  lines.push("");
  lines.push("  classDef high fill:#fee2e2,stroke:#dc2626,stroke-width:2px,color:#7f1d1d");
  lines.push("  classDef medium fill:#fef3c7,stroke:#d97706,stroke-width:2px,color:#78350f");
  lines.push("  classDef low fill:#dcfce7,stroke:#16a34a,stroke-width:2px,color:#14532d");
  lines.push("  classDef completed fill:#d1fae5,stroke:#059669,stroke-width:2px,color:#064e3b");
  lines.push("  classDef failed fill:#fecaca,stroke:#991b1b,stroke-width:3px,color:#7f1d1d");
  lines.push("  classDef fallback fill:#f3e8ff,stroke:#9333ea,stroke-width:1px,stroke-dasharray:4 4,color:#581c87");

  return lines.join("\n");
}

/* ------------------------------------------------------------------ */
/*  Small icon components (inline SVG to avoid extra dependencies)     */
/* ------------------------------------------------------------------ */

function IconCode({ className }: { className?: string }) {
  return (
    <svg className={className} width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="16 18 22 12 16 6" />
      <polyline points="8 6 2 12 8 18" />
    </svg>
  );
}

function IconExpand({ className }: { className?: string }) {
  return (
    <svg className={className} width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="15 3 21 3 21 9" />
      <polyline points="9 21 3 21 3 15" />
      <line x1="21" y1="3" x2="14" y2="10" />
      <line x1="3" y1="21" x2="10" y2="14" />
    </svg>
  );
}

function IconClose({ className }: { className?: string }) {
  return (
    <svg className={className} width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="18" y1="6" x2="6" y2="18" />
      <line x1="6" y1="6" x2="18" y2="18" />
    </svg>
  );
}

/* ------------------------------------------------------------------ */
/*  Fullscreen modal overlay                                           */
/* ------------------------------------------------------------------ */

function FullscreenModal({
  svgHtml,
  onClose,
}: {
  svgHtml: string;
  onClose: () => void;
}) {
  // Close on Escape key
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="relative w-[95vw] h-[90vh] bg-white dark:bg-zinc-900 rounded-xl shadow-2xl overflow-auto p-6"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Close button */}
        <button
          onClick={onClose}
          className="absolute top-3 right-3 p-1.5 rounded-md hover:bg-zinc-100 dark:hover:bg-zinc-800 text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200 transition-colors"
          title="Close (Esc)"
        >
          <IconClose />
        </button>

        {/* SVG content — full size, no width clamping */}
        <div
          className="w-full h-full flex items-start justify-center overflow-auto"
          dangerouslySetInnerHTML={{
            __html: svgHtml
              .replace(/\s+width="100%"/, ' width="100%"')
              .replace(/\s+height="auto"/, ' height="auto"'),
          }}
        />
      </div>
    </div>
  );
}

/**
 * ExecutionGraphRenderer: Renders execution_graph as a Mermaid flowchart.
 *
 * Key implementation notes:
 * - Uses dynamic import to avoid SSR issues with mermaid
 * - mermaid.render(id, text) returns { svg } — we set innerHTML directly
 * - Do NOT pass a 3rd container arg to mermaid.render in v10 (causes React DOM conflicts)
 * - The visibleRef div is managed by React; we only set its innerHTML, never appendChild
 *
 * Toolbar buttons:
 * - "Source": toggles display of the raw Mermaid diagram source
 * - "Fullscreen": opens the rendered SVG in a fullscreen modal overlay
 */
export const ExecutionGraphRenderer = React.memo(
  function ExecutionGraphRenderer({
    steps,
    className,
  }: ExecutionGraphRendererProps) {
    // svgHtml stores the rendered SVG string — set via mermaid.render, displayed via dangerouslySetInnerHTML
    const [svgHtml, setSvgHtml] = useState<string | null>(null);
    const [renderState, setRenderState] = useState<"idle" | "loading" | "done" | "error">("idle");
    const [errorMsg, setErrorMsg] = useState<string | null>(null);
    // Mermaid source text for the "View Source" feature
    const [diagramSource, setDiagramSource] = useState<string>("");
    const [showSource, setShowSource] = useState(false);
    const [showFullscreen, setShowFullscreen] = useState(false);
    // Stable unique ID per component instance (React 18+)
    const uid = useId().replace(/:/g, "");
    // Use a counter-based id to avoid Mermaid caching issues on re-render
    const renderCountRef = useRef(0);

    const handleCloseFullscreen = useCallback(() => setShowFullscreen(false), []);

    useEffect(() => {
      if (!steps || steps.length === 0) return;

      let cancelled = false;
      renderCountRef.current += 1;
      const currentCount = renderCountRef.current;
      const diagramId = `mermaid-egr-${uid}-${currentCount}`;

      setRenderState("loading");
      setErrorMsg(null);

      const renderDiagram = async () => {
        try {
          // Dynamic import of mermaid.
          // mermaid/package.json "exports['.']" is patched to point to
          // mermaid.esm.min.mjs (pre-built self-contained bundle) instead of
          // mermaid.core.mjs (non-self-contained ESM that imports dozens of
          // bare specifiers like d3, dayjs, etc. which webpack can't chunk
          // properly in Next.js dev mode, causing a 404).
          let mermaid;
          let lastError: unknown;
          for (let attempt = 0; attempt < 3; attempt++) {
            try {
              mermaid = (await import("mermaid")).default;
              lastError = undefined;
              break;
            } catch (e) {
              lastError = e;
              if (attempt < 2) {
                await new Promise((r) => setTimeout(r, 1000 * (attempt + 1)));
              }
            }
          }
          if (!mermaid) throw lastError;
          if (cancelled) return;

          mermaid.initialize({
            startOnLoad: false,
            theme: "default",
            securityLevel: "loose",
            flowchart: {
              useMaxWidth: true,
              htmlLabels: false,
              curve: "basis",
            },
          });

          const diagram = generateMermaidDiagram(steps);
          setDiagramSource(diagram);

          // mermaid v10: render(id, text) — only 2 args, returns { svg, bindFunctions }
          // Do NOT pass a container element — that causes React DOM removeChild errors
          // The id must be unique per render call to avoid Mermaid internal caching issues
          const { svg } = await mermaid.render(diagramId, diagram);

          if (cancelled) return;

          // Make SVG responsive by patching width/height attributes
          const responsiveSvg = svg
            .replace(/\s+width="[^"]*"/, ' width="100%"')
            .replace(/\s+height="[^"]*"/, ' height="auto"');

          setSvgHtml(responsiveSvg);
          setRenderState("done");
        } catch (err) {
          if (cancelled) return;
          const msg = err instanceof Error ? err.message : String(err);
          setErrorMsg(msg);
          setRenderState("error");
          // Still generate the diagram source so it can be shown in error state
          try {
            setDiagramSource(generateMermaidDiagram(steps));
          } catch {
            // ignore
          }
          console.error("[ExecutionGraphRenderer] Mermaid render error:", err);
        }
      };

      renderDiagram();

      return () => { cancelled = true; };
    // steps identity changes trigger re-render; uid is stable
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [steps]);

    if (!steps || steps.length === 0) {
      return null;
    }

    return (
      <div className={cn("space-y-2", className)}>
        {/* Error state */}
        {renderState === "error" && errorMsg && (
          <div className="text-xs text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/20 p-2 rounded border border-red-200 dark:border-red-800">
            ⚠ Diagram render error: {errorMsg}
          </div>
        )}

        {/* Loading state — separate from SVG container to avoid React clearing innerHTML */}
        {(renderState === "loading" || renderState === "idle") && (
          <div className="min-h-[100px] flex items-center justify-center rounded-lg border border-blue-100 dark:border-blue-900/40 bg-white dark:bg-zinc-950">
            <div className="text-[11px] text-zinc-400 animate-pulse">Rendering graph…</div>
          </div>
        )}

        {/* SVG container — only rendered when done, uses dangerouslySetInnerHTML to avoid React DOM conflicts */}
        {renderState === "done" && svgHtml && (
          <div
            className="overflow-x-auto rounded-lg border border-blue-100 dark:border-blue-900/40 bg-white dark:bg-zinc-950 p-3"
            dangerouslySetInnerHTML={{ __html: svgHtml }}
          />
        )}

        {/* Toolbar: Source + Fullscreen buttons */}
        {(renderState === "done" || (renderState === "error" && diagramSource)) && (
          <div className="flex items-center gap-1.5 px-1">
            {/* View Source toggle */}
            <button
              onClick={() => setShowSource((v) => !v)}
              className={cn(
                "inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-medium transition-colors",
                showSource
                  ? "bg-blue-100 dark:bg-blue-900/40 text-blue-700 dark:text-blue-300"
                  : "bg-zinc-100 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400 hover:bg-zinc-200 dark:hover:bg-zinc-700"
              )}
              title="Toggle Mermaid source code"
            >
              <IconCode />
              {showSource ? "Hide Source" : "Source"}
            </button>

            {/* Fullscreen button — only when SVG is available */}
            {renderState === "done" && svgHtml && (
              <button
                onClick={() => setShowFullscreen(true)}
                className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-medium bg-zinc-100 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400 hover:bg-zinc-200 dark:hover:bg-zinc-700 transition-colors"
                title="View diagram fullscreen"
              >
                <IconExpand />
                Fullscreen
              </button>
            )}
          </div>
        )}

        {/* Mermaid source code block */}
        {showSource && diagramSource && (
          <pre className="text-[11px] leading-relaxed bg-zinc-50 dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 rounded-lg p-3 overflow-x-auto max-h-[300px] overflow-y-auto font-mono text-zinc-700 dark:text-zinc-300 whitespace-pre">
            {diagramSource}
          </pre>
        )}

        {/* Legend */}
        {renderState === "done" && (
          <div className="flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-zinc-500 dark:text-zinc-400 px-1">
            <span className="flex items-center gap-1">
              <span className="inline-block w-2.5 h-2.5 rounded-sm bg-red-200 border border-red-600" />
              High
            </span>
            <span className="flex items-center gap-1">
              <span className="inline-block w-2.5 h-2.5 rounded-sm bg-yellow-100 border border-yellow-600" />
              Medium
            </span>
            <span className="flex items-center gap-1">
              <span className="inline-block w-2.5 h-2.5 rounded-sm bg-green-100 border border-green-600" />
              Low
            </span>
            <span className="flex items-center gap-1">
              <span className="inline-block w-2.5 h-2.5 rounded-sm bg-purple-100 border border-purple-500 border-dashed" />
              Fallback
            </span>
          </div>
        )}

        {/* Fullscreen modal */}
        {showFullscreen && svgHtml && (
          <FullscreenModal svgHtml={svgHtml} onClose={handleCloseFullscreen} />
        )}
      </div>
    );
  }
);

ExecutionGraphRenderer.displayName = "ExecutionGraphRenderer";
