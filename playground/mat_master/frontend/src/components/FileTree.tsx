"use client";

import { useCallback, useEffect, useState } from "react";
import { RefreshCwIcon } from "./icons";

const API_BASE =
  typeof window !== "undefined"
    ? (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000")
    : "";

export type RunItem = { id: string; label: string };
export type FileEntry = { name: string; path: string; dir: boolean };

/** File tree shows: runs/mat_master_web/workspaces/<last_task_id>/
 *  i.e. the workspace of the session's *most recent run*. Refetches on session/path change or manual refresh. */
export default function FileTree({
  sessionId,
  filePath,
  onFilePathChange,
  compact = false,
}: {
  sessionId: string | null;
  filePath: string;
  onFilePathChange: (path: string) => void;
  compact?: boolean;
}) {
  const [entries, setEntries] = useState<FileEntry[]>([]);
  const [taskId, setTaskId] = useState<string | null>(null);
  const [refetchTick, setRefetchTick] = useState(0);

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
  }, [fetchEntries, refetchTick]);

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
            className={`py-1 ${e.dir ? "text-zinc-600 dark:text-zinc-400 cursor-pointer hover:text-zinc-900 dark:hover:text-zinc-100" : "text-zinc-700 dark:text-zinc-300"}`}
            onClick={() => e.dir && openDir(e)}
          >
            {e.dir ? `${e.name}/` : e.name}
          </div>
        ))}
      </div>
    </div>
  );
}
