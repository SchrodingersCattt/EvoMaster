"use client";

import { useCallback, useEffect } from "react";

export type RunItem = { id: string; label: string };
export type FileEntry = { name: string; path: string; dir: boolean };

export default function FileTree({
  selectedRunId,
  onRunIdChange,
  runIds,
  onLoadRuns,
  filePath,
  onFilePathChange,
  entries,
  onLoadEntries,
}: {
  selectedRunId: string;
  onRunIdChange: (id: string) => void;
  runIds: RunItem[];
  onLoadRuns: () => void;
  filePath: string;
  onFilePathChange: (path: string) => void;
  entries: FileEntry[];
  onLoadEntries: (runId: string, path: string) => void;
}) {
  useEffect(() => {
    onLoadEntries(selectedRunId, filePath);
  }, [selectedRunId, filePath, onLoadEntries]);

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
      <h2 className="text-sm font-semibold mb-2 text-[#1e293b]">Runs / 文件</h2>
      <select
        value={selectedRunId}
        onChange={(e) => onRunIdChange(e.target.value)}
        className="w-full rounded border border-gray-300 px-2 py-1 text-sm mb-2 bg-white text-[#1f2937]"
      >
        {runIds.map((r) => (
          <option key={r.id} value={r.id}>
            {r.label}
          </option>
        ))}
      </select>
      <div className="text-xs text-gray-600 mb-1 truncate">
        {selectedRunId}{filePath ? ` / ${filePath}` : ""}
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
