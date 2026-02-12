"use client";

import { useEffect, useRef, useState } from "react";
import {
  ChevronDownIcon,
  ChevronRightIcon,
  FileCodeIcon,
  ActivityIcon,
  ListOrderedIcon,
  CircleDotIcon,
  CircleCheckIcon,
  CircleXIcon,
  RefreshCwIcon,
} from "./icons";
import { cn } from "@/lib/utils";
import FileTree from "./FileTree";
import type { LogEntry } from "./LogStream";
import type { FileEntry } from "./FileTree";
import { renderContent, renderMarkdown } from "./ContentRenderer";
import { isEnvRelatedEntry } from "@/lib/logEntryUtils";

const API_BASE =
  typeof window !== "undefined"
    ? (process.env.NEXT_PUBLIC_API_URL || "http://localhost:50001")
    : "";

const IMAGE_EXT = new Set([".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".ico"]);
const PDF_EXT = new Set([".pdf"]);
const MOLECULE_EXT = new Set([".xyz", ".mol", ".cif", ".vasp"]);
const MARKDOWN_EXT = new Set([".md", ".markdown"]);
const TEXT_EXT = new Set([
  ".txt",
  ".log",
  ".csv",
  ".tsv",
  ".json",
  ".yaml",
  ".yml",
  ".toml",
  ".ini",
  ".env",
  ".py",
  ".js",
  ".jsx",
  ".ts",
  ".tsx",
  ".html",
  ".css",
  ".mdx",
  ".sql",
  ".java",
  ".go",
  ".rs",
  ".sh",
  ".bash",
  ".ps1",
  ".rb",
  ".php",
  ".c",
  ".cc",
  ".cpp",
  ".h",
  ".hpp",
  ".xml",
  ".proto",
]);

function isImagePath(path: string): boolean {
  const clean = path.replace(/\?.*$/, "").toLowerCase();
  const i = clean.lastIndexOf(".");
  return i >= 0 && IMAGE_EXT.has(clean.slice(i));
}

function getFileExt(path: string): string {
  const clean = path.replace(/\?.*$/, "").toLowerCase();
  const i = clean.lastIndexOf(".");
  return i >= 0 ? clean.slice(i) : "";
}

function isMarkdownPath(path: string): boolean {
  return MARKDOWN_EXT.has(getFileExt(path));
}

function isTextPath(path: string): boolean {
  const ext = getFileExt(path);
  return MARKDOWN_EXT.has(ext) || TEXT_EXT.has(ext);
}

function isPdfPath(path: string): boolean {
  return PDF_EXT.has(getFileExt(path));
}

function isPoscarPath(path: string): boolean {
  const clean = path.replace(/\?.*$/, "").toLowerCase();
  const name = clean.split(/[/\\]/).pop() ?? "";
  return name === "poscar";
}

function isMoleculePath(path: string): boolean {
  return MOLECULE_EXT.has(getFileExt(path)) || isPoscarPath(path);
}

function inferMoleculeFormat(path: string): string {
  if (isPoscarPath(path)) return "vasp";
  const ext = getFileExt(path);
  if (ext === ".xyz") return "xyz";
  if (ext === ".mol") return "sdf";
  if (ext === ".cif") return "cif";
  if (ext === ".vasp") return "vasp";
  return "xyz";
}

type NglAtom = { element?: string };
type NglStructure = { eachAtom: (callback: (atom: NglAtom) => void) => void };
type NglComponent = {
  addRepresentation: (name: string, params?: Record<string, unknown>) => void;
  autoView: () => void;
  structure?: NglStructure;
};
type NglStage = {
  loadFile: (
    input: Blob | string,
    params?: { ext?: string; defaultRepresentation?: boolean }
  ) => Promise<NglComponent>;
  removeAllComponents: () => void;
  setParameters: (params: Record<string, unknown>) => void;
  handleResize: () => void;
  dispose: () => void;
};

declare global {
  interface Window {
    NGL?: {
      Stage: new (
        element: HTMLElement,
        params?: Record<string, unknown>
      ) => NglStage;
      ColormakerRegistry: {
        addScheme: (factory: (this: { atomColor: (atom: { element?: string }) => number }) => void) => string;
      };
    };
  }
}

const ELEMENT_COLORS: Record<string, string> = {
  H: "#FFFFFF",
  C: "#909090",
  N: "#3050F8",
  O: "#FF0D0D",
  F: "#90E050",
  Cl: "#1FF01F",
  Br: "#A62929",
  I: "#940094",
  S: "#FFFF30",
  P: "#FF8000",
  B: "#FFB5B5",
  Si: "#F0C8A0",
  Na: "#AB5CF2",
  K: "#8F40D4",
  Ca: "#3DFF00",
  Fe: "#E06633",
  Cu: "#C88033",
  Zn: "#7D80B0",
  Y: "#94FFFF",
  Ce: "#FFFFC7",
};

const ELEMENT_SYMBOLS = [
  "H", "He", "Li", "Be", "B", "C", "N", "O", "F", "Ne",
  "Na", "Mg", "Al", "Si", "P", "S", "Cl", "Ar", "K", "Ca",
  "Sc", "Ti", "V", "Cr", "Mn", "Fe", "Co", "Ni", "Cu", "Zn",
  "Ga", "Ge", "As", "Se", "Br", "Kr", "Rb", "Sr", "Y", "Zr",
  "Nb", "Mo", "Tc", "Ru", "Rh", "Pd", "Ag", "Cd", "In", "Sn",
  "Sb", "Te", "I", "Xe", "Cs", "Ba", "La", "Ce", "Pr", "Nd",
  "Pm", "Sm", "Eu", "Gd", "Tb", "Dy", "Ho", "Er", "Tm", "Yb",
  "Lu", "Hf", "Ta", "W", "Re", "Os", "Ir", "Pt", "Au", "Hg",
  "Tl", "Pb", "Bi", "Po", "At", "Rn", "Fr", "Ra", "Ac", "Th",
  "Pa", "U", "Np", "Pu", "Am", "Cm", "Bk", "Cf", "Es", "Fm",
  "Md", "No", "Lr", "Rf", "Db", "Sg", "Bh", "Hs", "Mt", "Ds",
  "Rg", "Cn", "Nh", "Fl", "Mc", "Lv", "Ts", "Og",
];
const UPPER_TO_CANONICAL_ELEMENT = new Map(
  ELEMENT_SYMBOLS.map((s) => [s.toUpperCase(), s] as const)
);

function canonicalizeElementSymbol(raw: string): string {
  const t = (raw || "").trim();
  if (!t) return "";
  return UPPER_TO_CANONICAL_ELEMENT.get(t.toUpperCase()) ?? t;
}

function elementColor(el: string): string {
  return ELEMENT_COLORS[canonicalizeElementSymbol(el)] ?? "#64748b";
}

function normalizeCifElementSymbols(raw: string): string {
  // Some CIFs contain all-caps type symbols (e.g., CE), which can break rendering.
  return raw.replace(/\b([A-Z]{1,2})\b/g, (token) => {
    return UPPER_TO_CANONICAL_ELEMENT.get(token) ?? token;
  });
}

function hexToColorValue(hex: string): number {
  const clean = hex.replace("#", "");
  const parsed = Number.parseInt(clean, 16);
  return Number.isFinite(parsed) ? parsed : 0x64748b;
}

function MoleculePreview({
  content,
  format,
  filePath,
}: {
  content: string;
  format: string;
  filePath: string;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const stageRef = useRef<NglStage | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [elements, setElements] = useState<string[]>([]);
  const isCrystal = format === "cif" || format === "vasp";

  useEffect(() => {
    let cancelled = false;
    const onResize = () => stageRef.current?.handleResize();
    window.addEventListener("resize", onResize);

    const renderMolecule = async () => {
      if (!containerRef.current) return;
      setLoadError(null);
      setElements([]);
      try {
        stageRef.current?.dispose();
        stageRef.current = null;
        containerRef.current.innerHTML = "";
        if (!window.NGL) {
          await new Promise<void>((resolve, reject) => {
            const existing = document.querySelector(
              'script[data-ngl="true"]'
            ) as HTMLScriptElement | null;
            if (existing) {
              if ((existing as { dataset?: DOMStringMap }).dataset?.loaded === "true") {
                resolve();
                return;
              }
              existing.addEventListener("load", () => resolve(), { once: true });
              existing.addEventListener("error", () => reject(new Error("NGL load failed")), {
                once: true,
              });
              return;
            }
            const script = document.createElement("script");
            script.src = "https://unpkg.com/ngl@2.1.1/dist/ngl.js";
            script.async = true;
            script.dataset["ngl"] = "true";
            script.onload = () => {
              script.dataset.loaded = "true";
              resolve();
            };
            script.onerror = () => reject(new Error("NGL load failed"));
            document.body.appendChild(script);
          });
        }
        if (cancelled || !containerRef.current || !window.NGL) return;
        const stage = new window.NGL.Stage(containerRef.current, {
          backgroundColor: "white",
          quality: "high",
        });
        stageRef.current = stage;

        const ext = format === "sdf" ? "mol" : format;
        const preparedContent =
          ext === "cif" ? normalizeCifElementSymbols(content) : content;
        const blob = new Blob([preparedContent], { type: "text/plain" });
        const component = await stage.loadFile(blob, {
          ext,
          defaultRepresentation: false,
        });
        const elementColorScheme = window.NGL.ColormakerRegistry.addScheme(function () {
          this.atomColor = (atom: { element?: string }) => {
            return hexToColorValue(elementColor(atom.element ?? ""));
          };
        });
        if (isCrystal) {
          // Crystal tuning: smaller atoms + thicker bonds + thin black unit cell.
          component.addRepresentation("spacefill", {
            color: elementColorScheme,
            radiusScale: 0.24,
          });
          component.addRepresentation("licorice", {
            color: elementColorScheme,
            radius: 0.32,
            multipleBond: "symmetric",
          });
          component.addRepresentation("unitcell", {
            colorValue: 0x000000,
            linewidth: 1,
          });
        } else {
          component.addRepresentation("ball+stick", {
            multipleBond: "symmetric",
            radiusScale: 0.9,
            color: elementColorScheme,
          });
          // Fallback visibility layer in case bond perception fails.
          component.addRepresentation("spacefill", {
            color: elementColorScheme,
            radiusScale: 0.25,
            opacity: 0.85,
          });
        }
        component.autoView();
        stage.handleResize();

        const elSet = new Set<string>();
        let atomCount = 0;
        component.structure?.eachAtom((atom) => {
          atomCount += 1;
          const el = canonicalizeElementSymbol(atom.element ?? "");
          if (el) elSet.add(el);
        });
        if (atomCount === 0) {
          throw new Error("结构中未解析到原子，可能是文件格式不兼容");
        }
        setElements(Array.from(elSet).sort((a, b) => a.localeCompare(b)));
      } catch (err) {
        if (!cancelled) {
          setLoadError(err instanceof Error ? err.message : "分子预览失败");
        }
      }
    };

    void renderMolecule();
    return () => {
      cancelled = true;
      window.removeEventListener("resize", onResize);
      stageRef.current?.dispose();
      stageRef.current = null;
    };
  }, [content, format, isCrystal, filePath]);

  if (loadError) {
    return <p className="text-xs text-amber-600 dark:text-amber-400">{loadError}</p>;
  }
  return (
    <div className="relative w-full h-[260px] rounded border border-zinc-200 overflow-hidden bg-white">
      <div ref={containerRef} className="w-full h-full" />
      {isCrystal && (
        <span className="absolute top-2 left-2 text-[10px] px-1.5 py-0.5 rounded bg-zinc-900/75 text-white">
          Unit Cell On
        </span>
      )}
      {elements.length > 0 && (
        <div className="absolute top-2 right-2 max-w-[45%] max-h-[85%] overflow-auto rounded bg-white/90 border border-zinc-300 px-2 py-1.5 text-[10px] text-zinc-700 shadow-sm">
          <div className="font-semibold mb-1">Elements</div>
          <div className="space-y-1">
            {elements.map((el) => (
              <div key={el} className="flex items-center gap-1.5">
                <span
                  className="inline-block w-2.5 h-2.5 rounded-full border border-zinc-400"
                  style={{ backgroundColor: elementColor(el) }}
                />
                <span>{el}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function FileViewer({
  sessionId,
  filePath,
  fileName,
  onClose,
}: {
  sessionId: string;
  filePath: string;
  fileName: string;
  onClose: () => void;
}) {
  const contentUrl = `${API_BASE}/api/sessions/${encodeURIComponent(sessionId)}/files/content?path=${encodeURIComponent(filePath)}`;
  const showImage = isImagePath(filePath);
  const showPdf = isPdfPath(filePath);
  const showMolecule = isMoleculePath(filePath);
  const [textContent, setTextContent] = useState<string | null>(null);
  const [textLoading, setTextLoading] = useState(false);
  const [textError, setTextError] = useState<string | null>(null);
  const [pdfBlobUrl, setPdfBlobUrl] = useState<string | null>(null);
  const [moleculeContent, setMoleculeContent] = useState<string | null>(null);
  const [moleculeLoading, setMoleculeLoading] = useState(false);
  const [moleculeError, setMoleculeError] = useState<string | null>(null);

  useEffect(() => {
    if (!sessionId) return;
    if (showImage || showPdf || showMolecule) {
      setTextContent(null);
      setTextError(null);
      return;
    }
    if (!isTextPath(filePath)) {
      setTextContent(null);
      setTextError(null);
      return;
    }
    let active = true;
    setTextLoading(true);
    setTextError(null);
    fetch(contentUrl)
      .then((r) => {
        if (!r.ok) throw new Error("fetch failed");
        return r.text();
      })
      .then((text) => {
        if (active) setTextContent(text);
      })
      .catch((err) => {
        if (active) {
          setTextContent(null);
          setTextError(err instanceof Error ? err.message : "预览失败");
        }
      })
      .finally(() => {
        if (active) setTextLoading(false);
      });
    return () => {
      active = false;
    };
  }, [contentUrl, filePath, sessionId, showImage, showPdf, showMolecule]);

  useEffect(() => {
    if (!sessionId || !showPdf) {
      setPdfBlobUrl((prev) => {
        if (prev) URL.revokeObjectURL(prev);
        return null;
      });
      return;
    }
    let active = true;
    fetch(contentUrl)
      .then((r) => {
        if (!r.ok) throw new Error("pdf fetch failed");
        return r.blob();
      })
      .then((blob) => {
        if (!active) return;
        setPdfBlobUrl((prev) => {
          if (prev) URL.revokeObjectURL(prev);
          return URL.createObjectURL(blob);
        });
      })
      .catch(() => {
        if (active) setPdfBlobUrl(null);
      });
    return () => {
      active = false;
    };
  }, [contentUrl, sessionId, showPdf]);

  useEffect(() => {
    if (!sessionId || !showMolecule) {
      setMoleculeContent(null);
      setMoleculeError(null);
      setMoleculeLoading(false);
      return;
    }
    let active = true;
    setMoleculeLoading(true);
    setMoleculeError(null);
    setMoleculeContent(null);
    fetch(contentUrl)
      .then((r) => {
        if (!r.ok) throw new Error("fetch failed");
        return r.text();
      })
      .then((text) => {
        if (active) setMoleculeContent(text);
      })
      .catch((err) => {
        if (active) {
          setMoleculeContent(null);
          setMoleculeError(err instanceof Error ? err.message : "分子预览失败");
        }
      })
      .finally(() => {
        if (active) setMoleculeLoading(false);
      });
    return () => {
      active = false;
    };
  }, [contentUrl, sessionId, showMolecule]);

  useEffect(() => {
    return () => {
      if (pdfBlobUrl) URL.revokeObjectURL(pdfBlobUrl);
    };
  }, [pdfBlobUrl]);

  return (
    <div className="border border-zinc-200 dark:border-zinc-800 rounded-md overflow-hidden bg-zinc-50 dark:bg-zinc-900/50">
      <div className="flex items-center justify-between gap-2 px-2 py-1.5 border-b border-zinc-200 dark:border-zinc-800">
        <span className="text-xs font-medium text-zinc-600 dark:text-zinc-400 truncate min-w-0" title={fileName}>
          {fileName}
        </span>
        <div className="flex items-center gap-1 shrink-0">
          <a
            href={contentUrl}
            download={fileName}
            className="text-xs px-2 py-1 rounded bg-zinc-200 dark:bg-zinc-700 hover:bg-zinc-300 dark:hover:bg-zinc-600 text-zinc-700 dark:text-zinc-300"
          >
            下载
          </a>
          <button
            type="button"
            onClick={onClose}
            className="text-xs px-2 py-1 rounded bg-zinc-200 dark:bg-zinc-700 hover:bg-zinc-300 dark:hover:bg-zinc-600 text-zinc-700 dark:text-zinc-300"
          >
            关闭
          </button>
        </div>
      </div>
      <div className="p-2 h-[280px] overflow-auto">
        {showImage ? (
          <img
            src={contentUrl}
            alt={fileName}
            className="max-w-full h-auto max-h-[260px] object-contain"
          />
        ) : showPdf ? (
          pdfBlobUrl ? (
            <iframe
              src={pdfBlobUrl}
              title={fileName}
              className="w-full h-[260px] bg-white dark:bg-zinc-900"
            />
          ) : (
            <p className="text-xs text-zinc-500 dark:text-zinc-400">加载 PDF 中...</p>
          )
        ) : showMolecule ? (
          moleculeLoading ? (
            <p className="text-xs text-zinc-500 dark:text-zinc-400">加载分子结构中...</p>
          ) : moleculeError ? (
            <p className="text-xs text-amber-600 dark:text-amber-400">{moleculeError}</p>
          ) : moleculeContent ? (
            <MoleculePreview
              key={filePath}
              filePath={filePath}
              content={moleculeContent}
              format={inferMoleculeFormat(filePath)}
            />
          ) : (
            <p className="text-xs text-zinc-500 dark:text-zinc-400">暂无可预览内容。</p>
          )
        ) : textLoading ? (
          <p className="text-xs text-zinc-500 dark:text-zinc-400">加载中...</p>
        ) : textContent !== null ? (
          isMarkdownPath(filePath) ? renderMarkdown(textContent) : renderContent(textContent)
        ) : textError ? (
          <p className="text-xs text-amber-600 dark:text-amber-400">{textError}</p>
        ) : (
          <p className="text-xs text-zinc-500 dark:text-zinc-400">请使用上方「下载」按钮保存文件。</p>
        )}
      </div>
    </div>
  );
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
  onJumpToLogIndex,
}: {
  entries: LogEntry[];
  sessionId: string | null;
  filePath: string;
  onFilePathChange: (path: string) => void;
  sessionFilesLogsKey?: number;
  readOnly?: boolean;
  onJumpToLogIndex?: (index: number) => void;
}) {
  const [selectedFile, setSelectedFile] = useState<{ path: string; name: string } | null>(null);
  const [fileTreeRefresh, setFileTreeRefresh] = useState(0);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const uploadFiles = async (files: FileList | null) => {
    if (!sessionId || !files || files.length === 0) return;
    setUploading(true);
    setUploadError(null);
    try {
      for (const file of Array.from(files)) {
        const form = new FormData();
        form.append("file", file);
        form.append("path", filePath || "");
        const res = await fetch(`${API_BASE}/api/sessions/${encodeURIComponent(sessionId)}/files/upload`, {
          method: "POST",
          body: form,
        });
        if (!res.ok) {
          const msg = await res.text();
          throw new Error(msg || "上传失败");
        }
      }
      setFileTreeRefresh((t) => t + 1);
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "上传失败");
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const toolResults = entries
    .map((e, index) => ({ entry: e, index }))
    .filter(
      ({ entry: e }) =>
      e.source === "ToolExecutor" &&
      e.type === "tool_result" &&
      !isEnvRelatedEntry(e)
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

  const phaseLabels: Record<string, { label: string; color: string }> = {
    planning: { label: "Planning", color: "bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-400" },
    preflight: { label: "Pre-flight", color: "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-400" },
    executing: { label: "Executing", color: "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-400" },
    replanning: { label: "Replanning", color: "bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-400" },
    completed: { label: "Completed", color: "bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-400" },
    failed: { label: "Failed", color: "bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-400" },
    aborted: { label: "Aborted", color: "bg-gray-100 text-gray-700 dark:bg-gray-900/40 dark:text-gray-400" },
  };

  const timelineEntries = entries.filter(
    (e) =>
      (e.source === "Planner" && e.type === "planner_reply") ||
      (e.source === "ToolExecutor" && e.type === "tool_result" && !isEnvRelatedEntry(e)) ||
      e.type === "phase_change" ||
      e.type === "replan_triggered" ||
      e.type === "plan_revised"
  );

  return (
    <div className="flex flex-col h-full min-h-0 bg-card border-r border-zinc-200 dark:border-zinc-800">
      <div className="flex-shrink-0 px-3 py-2 border-b border-zinc-200 dark:border-zinc-800">
        <h2 className="text-sm font-semibold text-zinc-800 dark:text-zinc-200">Workspace</h2>
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto p-3 space-y-3">
        {!readOnly && sessionId && (
          <AccordionSection title="Files" icon={FileCodeIcon} defaultOpen={true}>
            <div className="p-2 space-y-2">
              <div className="flex items-center gap-2 flex-wrap">
                <button
                  type="button"
                  onClick={() => fileInputRef.current?.click()}
                  className="text-xs px-2 py-1 rounded bg-zinc-200 dark:bg-zinc-700 hover:bg-zinc-300 dark:hover:bg-zinc-600 text-zinc-700 dark:text-zinc-300"
                  disabled={uploading}
                >
                  上传文件
                </button>
                <input
                  ref={fileInputRef}
                  type="file"
                  className="hidden"
                  multiple
                  onChange={(e) => uploadFiles(e.target.files)}
                />
                <span className="text-xs text-zinc-500 dark:text-zinc-400">
                  目标: {filePath ? filePath : "根目录"}
                </span>
                {uploading && <span className="text-xs text-zinc-500">上传中...</span>}
              </div>
              {uploadError && <p className="text-xs text-amber-600 dark:text-amber-400">{uploadError}</p>}
              {selectedFile && (
                <FileViewer
                  sessionId={sessionId}
                  filePath={selectedFile.path}
                  fileName={selectedFile.name}
                  onClose={() => setSelectedFile(null)}
                />
              )}
              <div className={cn("overflow-y-auto", selectedFile ? "max-h-[160px]" : "max-h-[240px]")}>
                <FileTree
                  key={`${sessionId}-${sessionFilesLogsKey}-${fileTreeRefresh}`}
                  sessionId={sessionId}
                  filePath={filePath}
                  onFilePathChange={onFilePathChange}
                  onFileSelect={(e) => setSelectedFile({ path: e.path || e.name, name: e.name })}
                  compact
                  refreshSignal={fileTreeRefresh}
                />
              </div>
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
                    {toolResults.map(({ entry: e, index }, i) => {
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
                          <button
                            type="button"
                            className="underline-offset-2 hover:underline text-left"
                            onClick={() => onJumpToLogIndex?.(index)}
                            title="跳转到右侧对话"
                          >
                            {c?.name ?? "—"}
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </>
            )}
            {mode === "planner" && (
              <>
                {/* Phase badge + replan count */}
                {lastPhase && (
                  <div className="flex items-center gap-2 flex-wrap mb-1">
                    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-semibold ${phaseLabels[lastPhase]?.color ?? "bg-gray-100 text-gray-700"}`}>
                      {phaseLabels[lastPhase]?.label ?? lastPhase}
                    </span>
                    {replanCount > 0 && (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-400">
                        <RefreshCwIcon size={10} className="shrink-0" />
                        Replan ×{replanCount}
                      </span>
                    )}
                  </div>
                )}
                {lastStages && (
                  <div className="font-medium text-zinc-700 dark:text-zinc-300">
                    Planner · Step {lastStages.current ?? "?"} / {lastStages.total ?? "?"}
                  </div>
                )}
                {lastStages?.intent && (
                  <p className="whitespace-pre-wrap break-words">{lastStages.intent}</p>
                )}
                {/* Replan trigger reason */}
                {lastReplan && lastPhase === "replanning" && (
                  <div className="mt-1 p-2 rounded-md bg-purple-50 dark:bg-purple-900/20 border border-purple-200 dark:border-purple-800">
                    <div className="flex items-center gap-1.5 text-[10px] font-semibold text-purple-700 dark:text-purple-400 uppercase tracking-wider">
                      <RefreshCwIcon size={12} className="shrink-0" />
                      Replan triggered
                    </div>
                    <p className="text-purple-600 dark:text-purple-300 mt-1">
                      {lastReplan.reason ?? "—"}
                      {lastReplan.after_step != null && (
                        <span className="text-purple-500"> (after step {lastReplan.after_step})</span>
                      )}
                    </p>
                  </div>
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
                {!lastStages && statusSkill.length === 0 && !lastPhase && (
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
                  const isPhaseChange = entry.type === "phase_change";
                  const isReplanTriggered = entry.type === "replan_triggered";
                  const isPlanRevised = entry.type === "plan_revised";
                  const isMetaEvent = isPhaseChange || isReplanTriggered || isPlanRevised;

                  if (isMetaEvent) {
                    const content = entry.content as Record<string, unknown>;
                    let label = "";
                    let color = "text-zinc-500 dark:text-zinc-400";
                    let icon = <CircleDotIcon size={14} className="text-zinc-400" />;

                    if (isPhaseChange) {
                      const to = String(content?.to ?? "");
                      const phaseInfo = phaseLabels[to];
                      label = `${String(content?.from ?? "?")} → ${phaseInfo?.label ?? to}`;
                      if (to === "replanning") {
                        color = "text-purple-600 dark:text-purple-400";
                        icon = <RefreshCwIcon size={14} className="text-purple-500" />;
                      } else if (to === "completed") {
                        icon = <CircleCheckIcon size={14} className="text-emerald-500" />;
                        color = "text-emerald-600 dark:text-emerald-400";
                      } else if (to === "failed") {
                        icon = <CircleXIcon size={14} className="text-red-500" />;
                        color = "text-red-600 dark:text-red-400";
                      }
                    } else if (isReplanTriggered) {
                      label = `Replan: ${String(content?.reason ?? "unknown")}`;
                      color = "text-purple-600 dark:text-purple-400";
                      icon = <RefreshCwIcon size={14} className="text-purple-500" />;
                    } else if (isPlanRevised) {
                      const cnt = Number(content?.replan_count ?? 0);
                      const oldN = Number(content?.old_step_count ?? 0);
                      const newN = Number(content?.new_step_count ?? 0);
                      label = `Plan revised #${cnt} (${oldN} → ${newN} steps)`;
                      color = "text-purple-600 dark:text-purple-400";
                      icon = <RefreshCwIcon size={14} className="text-purple-500" />;
                    }

                    return (
                      <li key={i} className="flex gap-2 py-1.5 border-b border-zinc-100 dark:border-zinc-800 last:border-0">
                        <span className="shrink-0 mt-0.5">{icon}</span>
                        <div className={cn("min-w-0 flex-1 text-[11px] italic", color)}>
                          {label}
                        </div>
                      </li>
                    );
                  }

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
