/** @type {import('next').NextConfig} */
const nextConfig = {
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: "http://localhost:8200/api/:path*",
      },
      {
        source: "/healthz",
        destination: "http://localhost:8200/healthz",
      },
      {
        source: "/perplexica/:path*",
        destination: "http://localhost:3001/:path*",
      },
      {
        source: "/ollama-proxy/:path*",
        destination: "http://100.94.37.18:11434/:path*",
      },
    ];
  },
};

export default nextConfig;
