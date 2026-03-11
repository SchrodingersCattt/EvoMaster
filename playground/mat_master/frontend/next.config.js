/** @type {import('next').NextConfig} */
const nextConfig = {
  // Mermaid v10's package.json "exports['.']" points to mermaid.core.mjs which is a
  // non-self-contained ESM entry that imports dozens of bare specifiers (d3, dayjs, etc.).
  // Webpack in Next.js dev mode tries to bundle all of them, producing a chunk named
  // _app-pages-browser_node_modules_mermaid_dist_mermaid_core_mjs.js that 404s.
  //
  // Fix: use NormalModuleReplacementPlugin to redirect any request for mermaid.core.mjs
  // to mermaid-b92f6f74.js (the pre-built CJS bundle that mermaid.esm.min.mjs re-exports).
  // This is the actual self-contained bundle with all deps inlined.
  webpack: (config) => {
    const path = require("path");
    const { NormalModuleReplacementPlugin } = require("webpack");
    config.plugins.push(
      new NormalModuleReplacementPlugin(
        /mermaid[\\/]dist[\\/]mermaid\.core\.mjs$/,
        path.resolve(
          __dirname,
          "node_modules/mermaid/dist/mermaid-b92f6f74.js"
        )
      )
    );
    return config;
  },

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
