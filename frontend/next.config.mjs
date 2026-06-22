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
    ];
  },
};

export default nextConfig;
