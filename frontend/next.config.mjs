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
    ];
  },
};

export default nextConfig;
