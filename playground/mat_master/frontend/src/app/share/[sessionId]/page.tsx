import LogStream from "@/components/LogStream";

const API_BASE =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:50001";

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
        <h1 className="text-2xl font-bold mb-4">Share</h1>
        <p className="text-gray-600 dark:text-gray-400">
          Session {sessionId} not found, or no records available.
        </p>
      </main>
    );
  }

  return (
    <LogStream logs={history} readOnly />
  );
}
