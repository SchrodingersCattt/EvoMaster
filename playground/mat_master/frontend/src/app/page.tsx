import LogStream from "@/components/LogStream";

export default function Home() {
  return (
    <main className="h-screen flex flex-col overflow-hidden">
      <header className="flex-shrink-0 px-4 py-2 border-b border-gray-200 bg-[#f9fafb]">
        <h1 className="text-xl font-bold text-[#1e293b]">EvoMaster-Mat</h1>
      </header>
      <div className="flex-1 min-h-0 overflow-hidden">
        <LogStream />
      </div>
    </main>
  );
}
