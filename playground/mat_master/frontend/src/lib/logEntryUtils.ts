import type { LogEntry } from "@/components/LogStream";

/**
 * 与 env 命令相关的条目（tool_call 或 tool_result）不应在界面展示。
 * - tool_call: execute_bash 且 args.command === "env"
 * - tool_result: name/result/command/args 任一包含 "env"
 */
export function isEnvRelatedEntry(entry: LogEntry): boolean {
  if (!entry.content || typeof entry.content !== "object") return false;
  const c = entry.content as {
    name?: string;
    result?: string;
    command?: string;
    args?: string;
  };
  if (entry.type === "tool_call") {
    const argsStr = typeof c.args === "string" ? c.args : "";
    try {
      const args = JSON.parse(argsStr) as { command?: string } | null;
      if (args && typeof args.command === "string" && args.command.trim() === "env")
        return true;
    } catch {
      // not JSON or invalid
    }
    return false;
  }
  if (entry.type === "tool_result") {
    const s = [c.name, c.result, c.command, typeof c.args === "string" ? c.args : ""]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    return s.includes("env");
  }
  return false;
}
