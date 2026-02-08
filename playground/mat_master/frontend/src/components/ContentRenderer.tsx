"use client";

import React from "react";
import ReactMarkdown from "react-markdown";

function tryParseJSON(str: string): unknown {
  const t = str.trim();
  if (/^\s*[\{\[]/.test(t)) {
    try {
      return JSON.parse(str);
    } catch {
      return null;
    }
  }
  return null;
}

function looksLikeMarkdown(str: string): boolean {
  return /#\s|^\s*[-*+]\s|^\s*\d+\.\s|\*\*[^*]+\*\*|__[^_]+__|`[^`]+`|\[.+\]\(.+\)/m.test(str);
}

function JsonBlock({ data }: { data: unknown }) {
  const str = typeof data === "string" ? data : JSON.stringify(data, null, 2);
  return (
    <pre className="text-xs whitespace-pre-wrap break-words bg-gray-100 p-2 rounded overflow-x-auto text-[#1f2937] font-mono">
      {str}
    </pre>
  );
}

export function renderContent(content: unknown): React.ReactNode {
  if (content === null || content === undefined) {
    return <span className="text-gray-500 italic">(空)</span>;
  }
  if (typeof content === "string") {
    const text = content.trim();
    if (!text) return <span className="text-gray-500 italic">(无文本输出)</span>;
    const parsed = tryParseJSON(text);
    if (parsed !== null) {
      return <JsonBlock data={parsed} />;
    }
    if (looksLikeMarkdown(text)) {
      return (
        <div className="text-sm prose prose-sm max-w-none prose-p:my-1 prose-headings:my-2">
          <ReactMarkdown>{text}</ReactMarkdown>
        </div>
      );
    }
    return (
      <div className="text-sm whitespace-pre-wrap">{text}</div>
    );
  }
  if (typeof content === "object") {
    return (
      <pre className="text-xs whitespace-pre-wrap break-words bg-gray-100 p-2 rounded overflow-x-auto text-[#1f2937] font-mono">
        {JSON.stringify(content, null, 2)}
      </pre>
    );
  }
  return <span>{String(content)}</span>;
}
