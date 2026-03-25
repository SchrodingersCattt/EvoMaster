"use client";

import React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import rehypeHighlight from "rehype-highlight";
import "katex/dist/katex.min.css";

function tryParseJSON(str: string): unknown {
  const t = str.trim();
  if (!/^\s*[\{\[]/.test(t)) return null;
  try {
    return JSON.parse(str);
  } catch {
    return null;
  }
}

function looksLikeMarkdown(str: string): boolean {
  return /#\s|^\s*[-*+]\s|^\s*\d+\.\s|\*\*[^*]+\*\*|__[^_]+__|`[^`]+`|\[.+\]\(.+\)|\$\$|\\\(|\\\[/m.test(str);
}

const JsonBlock = React.memo(function JsonBlock({ data }: { data: unknown }) {
  const str = typeof data === "string" ? data : JSON.stringify(data, null, 2);
  return (
    <pre className="text-xs whitespace-pre-wrap break-words bg-zinc-100 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700 p-3 rounded-md overflow-x-auto text-zinc-800 dark:text-zinc-200 font-mono">
      {str}
    </pre>
  );
});

const STR_REPLACE_EDITOR_TOOL = "str_replace_editor";

function isStrReplaceEditorPayload(o: unknown): o is Record<string, unknown> {
  return (
    typeof o === "object" &&
    o !== null &&
    (o as Record<string, unknown>).tool === STR_REPLACE_EDITOR_TOOL &&
    typeof (o as Record<string, unknown>).kind === "string"
  );
}

/** 从 tool_result 外层（含 status/observation）或裸 payload 中取出编辑器结构化结果 */
function unwrapStrReplaceEditorPayload(content: unknown): Record<string, unknown> | null {
  if (!content || typeof content !== "object") return null;
  const c = content as Record<string, unknown>;
  if (isStrReplaceEditorPayload(c)) return c;
  const obs = c.observation;
  if (obs && typeof obs === "object" && isStrReplaceEditorPayload(obs)) {
    return obs as Record<string, unknown>;
  }
  const res = c.result;
  if (res && typeof res === "object") {
    const r = res as Record<string, unknown>;
    if (isStrReplaceEditorPayload(r)) return r;
    const inner = r.observation;
    if (inner && typeof inner === "object" && isStrReplaceEditorPayload(inner)) {
      return inner as Record<string, unknown>;
    }
  }
  return null;
}

const StrReplaceEditorPayloadView = React.memo(function StrReplaceEditorPayloadView({
  payload,
}: {
  payload: Record<string, unknown>;
}) {
  const kind = String(payload.kind ?? "");

  if (kind === "numbered_content") {
    const lines = (payload.lines as Array<{ line_no?: number; text?: string }>) ?? [];
    const descriptor = String(payload.descriptor ?? "");
    return (
      <div className="space-y-2">
        {descriptor ? (
          <div
            className="text-[11px] text-zinc-500 dark:text-zinc-400 font-mono truncate"
            title={descriptor}
          >
            {descriptor}
          </div>
        ) : null}
        <div className="border border-zinc-200 dark:border-zinc-700 rounded-md overflow-hidden text-xs font-mono">
          <table className="w-full border-collapse">
            <tbody>
              {lines.map((row, i) => (
                <tr
                  key={`${row.line_no ?? i}-${i}`}
                  className="border-b border-zinc-100 dark:border-zinc-800 last:border-0"
                >
                  <td className="align-top pr-2 py-0.5 text-right text-zinc-400 select-none w-12 shrink-0 bg-zinc-50 dark:bg-zinc-900/50">
                    {row.line_no ?? i + 1}
                  </td>
                  <td className="align-top py-0.5 text-zinc-800 dark:text-zinc-200 whitespace-pre-wrap break-words">
                    {row.text ?? ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  }

  if (kind === "directory_listing") {
    const listing = String(payload.listing ?? "");
    const path = String(payload.path ?? "");
    return (
      <div className="space-y-1">
        <div className="text-[11px] text-zinc-500 dark:text-zinc-400 font-mono truncate">{path}</div>
        <pre className="text-xs whitespace-pre-wrap break-words bg-zinc-100 dark:bg-zinc-800 p-2 rounded-md border border-zinc-200 dark:border-zinc-700">
          {listing}
        </pre>
      </div>
    );
  }

  if (kind === "edit_success") {
    const msg = String(payload.message ?? "");
    const snippet = payload.snippet;
    return (
      <div className="space-y-2">
        <div className="text-sm text-zinc-700 dark:text-zinc-300">{msg}</div>
        {snippet && typeof snippet === "object" && isStrReplaceEditorPayload(snippet) && (
          <StrReplaceEditorPayloadView payload={snippet as Record<string, unknown>} />
        )}
      </div>
    );
  }

  if (kind === "create_success") {
    return (
      <div className="text-sm text-zinc-700 dark:text-zinc-300 space-y-1">
        <div className="font-mono text-xs text-zinc-500 dark:text-zinc-400">
          {String(payload.path ?? "")}
          {payload.overwritten ? (
            <span className="ml-2 text-amber-600 dark:text-amber-400">(overwritten)</span>
          ) : null}
        </div>
        <div>{String(payload.message ?? "")}</div>
      </div>
    );
  }

  if (kind === "undo_success") {
    const restored = payload.restored;
    return (
      <div className="space-y-2">
        <div className="text-sm text-zinc-700 dark:text-zinc-300">{String(payload.message ?? "")}</div>
        {restored && typeof restored === "object" && isStrReplaceEditorPayload(restored) && (
          <StrReplaceEditorPayloadView payload={restored as Record<string, unknown>} />
        )}
      </div>
    );
  }

  return <JsonBlock data={payload} />;
});

const MarkdownContent = React.memo(function MarkdownContent({ text }: { text: string }) {
  try {
    return (
      <div className="content-renderer text-sm prose prose-sm dark:prose-invert max-w-none prose-p:my-1.5 prose-headings:my-2 prose-pre:bg-zinc-100 prose-pre:dark:bg-zinc-800 prose-pre:border prose-pre:border-zinc-200 prose-pre:dark:border-zinc-700 prose-pre:rounded-md prose-pre:text-xs">
        <ReactMarkdown
          remarkPlugins={[remarkGfm, remarkMath]}
          rehypePlugins={[rehypeKatex, rehypeHighlight]}
          components={{
            a({ href, children, ...props }) {
              return (
                <a
                  href={href}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-blue-800 dark:text-blue-400 hover:underline"
                  {...props}
                >
                  {children}
                </a>
              );
            },
            code({ className, children, ...props }) {
              return (
                <code className={className ?? "bg-zinc-100 dark:bg-zinc-800 px-1.5 py-0.5 rounded font-mono text-xs"} {...props}>
                  {children}
                </code>
              );
            },
            pre({ children }) {
              return <pre className="!my-2 overflow-x-auto">{children}</pre>;
            },
          }}
        >
          {text}
        </ReactMarkdown>
      </div>
    );
  } catch {
    return (
      <div className="text-sm whitespace-pre-wrap break-words text-zinc-600 dark:text-zinc-400">
        {text}
      </div>
    );
  }
});

export function renderMarkdown(text: string): React.ReactNode {
  return <MarkdownContent text={text} />;
}

/**
 * Componentized ContentRenderer with React.memo for performance.
 * Replaces the old `renderContent()` function approach.
 */
export const ContentRenderer = React.memo(function ContentRenderer({
  content,
}: {
  content: unknown;
}) {
  if (content === null || content === undefined) {
    return <span className="text-zinc-500 italic">(空)</span>;
  }
  if (typeof content === "string") {
    const text = content.trim();
    if (!text) return <span className="text-zinc-500 italic">(无文本输出)</span>;
    const parsed = tryParseJSON(text);
    if (parsed !== null) {
      const editor = unwrapStrReplaceEditorPayload(parsed);
      if (editor) {
        return <StrReplaceEditorPayloadView payload={editor} />;
      }
      return <JsonBlock data={parsed} />;
    }
    if (looksLikeMarkdown(text)) {
      return <MarkdownContent text={text} />;
    }
    return (
      <div className="text-sm whitespace-pre-wrap break-words text-zinc-700 dark:text-zinc-300">
        {text}
      </div>
    );
  }
  if (typeof content === "object") {
    const editor = unwrapStrReplaceEditorPayload(content);
    if (editor) {
      return <StrReplaceEditorPayloadView payload={editor} />;
    }
    return <JsonBlock data={content} />;
  }
  return <span>{String(content)}</span>;
});

/**
 * Backward-compatible function wrapper.
 * Delegates to the memoized <ContentRenderer /> component.
 */
export function renderContent(content: unknown): React.ReactNode {
  return <ContentRenderer content={content} />;
}
