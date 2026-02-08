"use client";

import { useCallback, useEffect, useState } from "react";

const API_BASE =
  typeof window !== "undefined"
    ? (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000")
    : "";

export type RunItem = { id: string; label: string };
export type FileEntry = { name: string; path: string; dir: boolean };

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

  useEffect(() => {
    if (!sessionId) {
      setEntries([]);
      return;
    }
    const url = `${API_BASE}/api/sessions/${encodeURIComponent(sessionId)}/files${filePath ? `?path=${encodeURIComponent(filePath)}` : ""}`;
    fetch(url)
      .then((r) => (r.ok ? r.json() : { entries: [] }))
      .then((d: { entries: FileEntry[] }) => setEntries(d.entries || []))
      .catch(() => setEntries([]));
  }, [sessionId, filePath]);

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
      {!compact && <h2 className="text-sm font-semibold mb-2 text-zinc-800 dark:text-zinc-200">当前会话文件</h2>}
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
