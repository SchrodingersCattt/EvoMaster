/** @type {import('next').NextConfig} */
const nextConfig = {
  // Mermaid is loaded as a static UMD script from /public/mermaid.js (copied from
  // node_modules/mermaid/dist/mermaid.min.js at build/setup time).
  // mermaid.min.js is the proper UMD build that sets window.mermaid = factory().
  // This bypasses webpack bundling entirely, eliminating the 404 chunk error that
  // occurred when webpack tried to bundle mermaid.core.mjs / mermaid.esm.min.mjs.
  // See: layout.tsx <Script src="/mermaid.js" strategy="beforeInteractive" />
  // See: ExecutionGraphRenderer.tsx uses window.mermaid instead of import("mermaid")

  // Allow cross-origin requests to /_next/* when accessed via domain (e.g. gjao1318755.bohrium.tech)
  allowedDevOrigins: [
    "localhost:50003",
    "127.0.0.1:50003",
    "101.126.90.82:50003",
    "gjao1318755.bohrium.tech",
    "gjao1318755.bohrium.tech:50003",
    "http://gjao1318755.bohrium.tech:50003",
  ],
  // Allow dev server / server actions from specific origins (e.g. server IP when deployed)
  experimental: {
    serverActions: {
      allowedOrigins: [
        "localhost:50003",
        "127.0.0.1:50003",
        "101.126.90.82:50003",
        "gjao1318755.bohrium.tech:50003",
      ],
    },
  },
};

module.exports = nextConfig;
