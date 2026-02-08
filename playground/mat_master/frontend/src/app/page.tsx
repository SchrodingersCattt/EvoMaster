import LogStream from "@/components/LogStream";

export default function Home() {
  return (
    <main className="min-h-screen p-6">
      <h1 className="text-2xl font-bold mb-4">MatMaster</h1>
      <p className="text-sm text-gray-600 dark:text-gray-400 mb-4">
        输入任务后，Agent 的思考与工具调用将实时流式展示。
      </p>
      <LogStream />
    </main>
  );
}
