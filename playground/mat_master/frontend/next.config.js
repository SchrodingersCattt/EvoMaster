/** @type {import('next').NextConfig} */
const nextConfig = {
  // Transpile mermaid ESM package so webpack can bundle it correctly in dev mode
  transpilePackages: ['mermaid'],
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
