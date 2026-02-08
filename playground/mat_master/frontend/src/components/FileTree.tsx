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
}: {
  sessionId: string | null;
  filePath: string;
  onFilePathChange: (path: string) => void;
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
    <div className="border border-gray-300 rounded-lg p-3 bg-[#f3f4f6] flex flex-col h-full min-h-0">
      <h2 className="text-sm font-semibold mb-2 text-[#1e293b]">当前会话文件</h2>
      <div className="text-xs text-gray-600 mb-1 truncate">
        {filePath ? filePath : "根目录"}
      </div>
      <div className="flex-1 overflow-y-auto text-sm min-h-0">
        {filePath && (
          <div
            className="py-1 text-[#1e40af] cursor-pointer"
            onClick={goUp}
          >
            ..
          </div>
        )}
        {entries.map((e) => (
          <div
            key={e.path || e.name}
            className={`py-1 ${e.dir ? "text-[#1e40af] cursor-pointer" : "text-[#1f2937]"}`}
            onClick={() => e.dir && openDir(e)}
          >
            {e.dir ? `${e.name}/` : e.name}
          </div>
        ))}
      </div>
    </div>
  );
}
