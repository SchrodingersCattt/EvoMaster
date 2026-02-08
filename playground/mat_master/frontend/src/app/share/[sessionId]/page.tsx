import LogStream from "@/components/LogStream";

const API_BASE =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function getHistory(sessionId: string): Promise<LogEntry[] | null> {
  try {
    const res = await fetch(`${API_BASE}/api/share/${sessionId}`, {
      cache: "no-store",
    });
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
}

type LogEntry = { source: string; type: string; content: unknown };

export default async function SharePage({
  params,
}: {
  params: Promise<{ sessionId: string }>;
}) {
  const { sessionId } = await params;
  const history = await getHistory(sessionId);

  if (!history || history.length === 0) {
    return (
      <main className="min-h-screen p-6">
        <h1 className="text-2xl font-bold mb-4">分享</h1>
        <p className="text-gray-600 dark:text-gray-400">
          未找到会话 {sessionId}，或该会话暂无记录。
        </p>
      </main>
    );
  }

  return (
    <main className="min-h-screen p-6">
      <h1 className="text-2xl font-bold mb-4">分享：{sessionId}</h1>
      <p className="text-sm text-gray-600 dark:text-gray-400 mb-4">只读回放</p>
      <LogStream logs={history} readOnly />
    </main>
  );
}
