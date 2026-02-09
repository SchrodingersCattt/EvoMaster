"use client";

import { useCallback, useEffect, useState } from "react";
import { RefreshCwIcon } from "./icons";

const API_BASE =
  typeof window !== "undefined"
    ? (process.env.NEXT_PUBLIC_API_URL || "http://localhost:50001")
    : "";

export type RunItem = { id: string; label: string };
export type FileEntry = { name: string; path: string; dir: boolean };

/** File tree shows: runs/mat_master_web/workspaces/<last_task_id>/
 *  i.e. the workspace of the session's *most recent run*. Refetches on session/path change or manual refresh. */
export default function FileTree({
  sessionId,
  filePath,
  onFilePathChange,
  onFileSelect,
  compact = false,
  refreshSignal,
}: {
  sessionId: string | null;
  filePath: string;
  onFilePathChange: (path: string) => void;
  onFileSelect?: (entry: FileEntry) => void;
  compact?: boolean;
  refreshSignal?: number;
}) {
  const [entries, setEntries] = useState<FileEntry[]>([]);
  const [taskId, setTaskId] = useState<string | null>(null);
  const [refetchTick, setRefetchTick] = useState(0);
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; entry: FileEntry } | null>(null);

  const fetchEntries = useCallback(() => {
    if (!sessionId) {
      setEntries([]);
      setTaskId(null);
      return;
    }
    const url = `${API_BASE}/api/sessions/${encodeURIComponent(sessionId)}/files${filePath ? `?path=${encodeURIComponent(filePath)}` : ""}`;
    fetch(url)
      .then((r) => (r.ok ? r.json() : { entries: [], task_id: null }))
      .then((d: { entries: FileEntry[]; task_id?: string | null }) => {
        setEntries(d.entries || []);
        setTaskId(d.task_id ?? null);
      })
      .catch(() => {
        setEntries([]);
        setTaskId(null);
      });
  }, [sessionId, filePath]);

  useEffect(() => {
    fetchEntries();
  }, [fetchEntries, refetchTick, refreshSignal]);

  useEffect(() => {
    const close = () => setContextMenu(null);
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setContextMenu(null);
    };
    window.addEventListener("click", close);
    window.addEventListener("scroll", close, true);
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("click", close);
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, []);

  const openDir = useCallback(
    (entry: FileEntry) => {
      if (entry.dir) onFilePathChange(entry.path || entry.name);
    },
    [onFilePathChange]
  );

  const goUp = useCallback(() => {
    const parts = filePath.split(/[/\\]/).filter(Boolean);
    parts.pop();
    onFilePathChange(parts.join("/"));
  }, [filePath, onFilePathChange]);

  const getEntryPath = useCallback((entry: FileEntry) => entry.path || entry.name, []);

  const handleDownload = useCallback(
    (entry: FileEntry) => {
      if (!sessionId || entry.dir) {
        setContextMenu(null);
        return;
      }
      const path = getEntryPath(entry);
      const url = `${API_BASE}/api/sessions/${encodeURIComponent(sessionId)}/files/content?path=${encodeURIComponent(path)}`;
      const link = document.createElement("a");
      link.href = url;
      link.download = entry.name;
      link.rel = "noopener";
      document.body.appendChild(link);
      link.click();
      link.remove();
      setContextMenu(null);
    },
    [getEntryPath, sessionId]
  );

  const handleCopyPath = useCallback(
    async (entry: FileEntry) => {
      const path = getEntryPath(entry);
      try {
        await navigator.clipboard.writeText(path);
      } catch {
        const textarea = document.createElement("textarea");
        textarea.value = path;
        textarea.style.position = "fixed";
        textarea.style.left = "-9999px";
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand("copy");
        textarea.remove();
      }
      setContextMenu(null);
    },
    [getEntryPath]
  );

  const handleRename = useCallback(
    async (entry: FileEntry) => {
      if (!sessionId) {
        setContextMenu(null);
        return;
      }
      const next = window.prompt("输入新名称", entry.name);
      if (!next || !next.trim() || next.trim() === entry.name) {
        setContextMenu(null);
        return;
      }
      const res = await fetch(`${API_BASE}/api/sessions/${encodeURIComponent(sessionId)}/files/rename`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: getEntryPath(entry), new_name: next.trim() }),
      });
      if (!res.ok) {
        const msg = await res.text();
        alert(`重命名失败: ${msg || res.status}`);
      } else {
        setRefetchTick((t) => t + 1);
      }
      setContextMenu(null);
    },
    [getEntryPath, sessionId]
  );

  return (
    <div className={compact ? "flex flex-col min-h-0" : "border border-zinc-200 dark:border-zinc-700 rounded-md p-3 bg-zinc-50 dark:bg-zinc-900/50 flex flex-col h-full min-h-0"}>
      {!compact && (
        <div className="flex items-center justify-between gap-2 mb-2">
          <h2 className="text-sm font-semibold text-zinc-800 dark:text-zinc-200">当前会话文件</h2>
          <button
            type="button"
            onClick={() => setRefetchTick((t) => t + 1)}
            className="p-1 rounded hover:bg-zinc-200 dark:hover:bg-zinc-700 text-zinc-500"
            title="刷新文件列表"
            aria-label="刷新"
          >
            <RefreshCwIcon size={14} />
          </button>
        </div>
      )}
      {taskId && (
        <div className="text-xs text-zinc-500 dark:text-zinc-400 mb-1 flex items-center gap-1">
          <span className="truncate flex-1 min-w-0" title={`runs/mat_master_web/workspaces/${taskId}`}>
            workspaces/{taskId}
          </span>
          {compact && (
            <button
              type="button"
              onClick={() => setRefetchTick((t) => t + 1)}
              className="p-0.5 rounded hover:bg-zinc-200 dark:hover:bg-zinc-700 shrink-0"
              title="刷新文件列表"
              aria-label="刷新"
            >
              <RefreshCwIcon size={12} />
            </button>
          )}
        </div>
      )}
      <div className="text-xs text-zinc-500 dark:text-zinc-400 mb-1 break-all">
        {filePath ? filePath : "根目录"}
      </div>
      <div className="flex-1 overflow-y-auto text-sm min-h-0">
        {filePath && (
          <div
            className="py-1 text-zinc-600 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-100 cursor-pointer"
            onClick={goUp}
          >
            ..
          </div>
        )}
        {entries.map((e) => (
          <div
            key={e.path || e.name}
            className={`py-1 ${e.dir ? "text-zinc-600 dark:text-zinc-400 cursor-pointer hover:text-zinc-900 dark:hover:text-zinc-100" : "text-zinc-700 dark:text-zinc-300 cursor-pointer hover:text-zinc-900 dark:hover:text-zinc-100"}`}
            onClick={() => {
              if (e.dir) openDir(e);
              else onFileSelect?.(e);
            }}
            onContextMenu={(event) => {
              event.preventDefault();
              setContextMenu({ x: event.clientX, y: event.clientY, entry: e });
            }}
          >
            {e.dir ? `${e.name}/` : e.name}
          </div>
        ))}
      </div>
      {contextMenu && (
        <div className="fixed inset-0 z-50" onClick={() => setContextMenu(null)}>
          <div
            className="absolute w-56 rounded-md border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 shadow-lg text-sm"
            style={{
              left:
                typeof window !== "undefined"
                  ? Math.min(contextMenu.x, window.innerWidth - 232)
                  : contextMenu.x,
              top:
                typeof window !== "undefined"
                  ? Math.min(contextMenu.y, window.innerHeight - 200)
                  : contextMenu.y,
            }}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="px-3 py-2 border-b border-zinc-100 dark:border-zinc-800">
              <div className="text-xs text-zinc-500 dark:text-zinc-400">相对路径</div>
              <div className="text-xs text-zinc-700 dark:text-zinc-300 break-all">
                {getEntryPath(contextMenu.entry)}
              </div>
            </div>
            <button
              type="button"
              onClick={() => handleCopyPath(contextMenu.entry)}
              className="w-full text-left px-3 py-2 hover:bg-zinc-100 dark:hover:bg-zinc-800"
            >
              复制相对路径
            </button>
            {!contextMenu.entry.dir && (
              <button
                type="button"
                onClick={() => handleDownload(contextMenu.entry)}
                className="w-full text-left px-3 py-2 hover:bg-zinc-100 dark:hover:bg-zinc-800"
              >
                下载
              </button>
            )}
            <button
              type="button"
              onClick={() => handleRename(contextMenu.entry)}
              className="w-full text-left px-3 py-2 hover:bg-zinc-100 dark:hover:bg-zinc-800"
            >
              重命名
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
