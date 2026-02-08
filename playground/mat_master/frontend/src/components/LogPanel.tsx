"use client";

export default function LogPanel() {
  return (
    <div className="border border-gray-300 rounded-lg p-3 bg-[#f3f4f6] flex flex-col min-h-[200px]">
      <h2 className="text-sm font-semibold mb-2 text-[#1e293b]">日志</h2>
      <div className="text-xs text-gray-500 flex-1 flex items-center justify-center">
        INFO / ERROR — 选择 Run 后加载
      </div>
    </div>
  );
}
