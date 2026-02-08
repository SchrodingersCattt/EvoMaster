/** @type {import('next').NextConfig} */
const nextConfig = {
  // Allow dev server / server actions from specific origins (e.g. server IP when deployed)
  experimental: {
    serverActions: {
      allowedOrigins: [
        "localhost:3000",
        "127.0.0.1:3000",
        "101.126.90.82:3000",
      ],
    },
  },
};

module.exports = nextConfig;
