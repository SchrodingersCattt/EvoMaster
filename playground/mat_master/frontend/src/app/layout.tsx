import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "EvoMaster-Mat",
  description: "EvoMaster MatMaster agent stream",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body className="antialiased min-h-screen">{children}</body>
    </html>
  );
}
