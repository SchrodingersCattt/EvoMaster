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

function JsonBlock({ data }: { data: unknown }) {
  const str = typeof data === "string" ? data : JSON.stringify(data, null, 2);
  return (
    <pre className="text-xs whitespace-pre-wrap break-words bg-zinc-100 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700 p-3 rounded-md overflow-x-auto text-zinc-800 dark:text-zinc-200 font-mono">
      {str}
    </pre>
  );
}

function MarkdownContent({ text }: { text: string }) {
  try {
    return (
      <div className="content-renderer text-sm prose prose-sm dark:prose-invert max-w-none prose-p:my-1.5 prose-headings:my-2 prose-pre:bg-zinc-100 prose-pre:dark:bg-zinc-800 prose-pre:border prose-pre:border-zinc-200 prose-pre:dark:border-zinc-700 prose-pre:rounded-md prose-pre:text-xs">
        <ReactMarkdown
          remarkPlugins={[remarkGfm, remarkMath]}
          rehypePlugins={[rehypeKatex, rehypeHighlight]}
          components={{
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
}

export function renderMarkdown(text: string): React.ReactNode {
  return <MarkdownContent text={text} />;
}

export function renderContent(content: unknown): React.ReactNode {
  if (content === null || content === undefined) {
    return <span className="text-zinc-500 italic">(空)</span>;
  }
  if (typeof content === "string") {
    const text = content.trim();
    if (!text) return <span className="text-zinc-500 italic">(无文本输出)</span>;
    const parsed = tryParseJSON(text);
    if (parsed !== null) {
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
    try {
      return (
        <pre className="text-xs whitespace-pre-wrap break-words bg-zinc-100 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700 p-3 rounded-md overflow-x-auto text-zinc-800 dark:text-zinc-200 font-mono">
          {JSON.stringify(content, null, 2)}
        </pre>
      );
    } catch {
      return <span className="text-zinc-500">(无法序列化)</span>;
    }
  }
  return <span>{String(content)}</span>;
}
