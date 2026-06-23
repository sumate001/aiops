/** @type {import('next').NextConfig} */
const nextConfig = {
  output: process.env.DOCKER_BUILD ? 'standalone' : undefined,
  async rewrites() {
    const apiBase = process.env.API_BASE_URL || 'http://localhost:8200';
    const perplexicaBase = process.env.PERPLEXICA_BASE_URL || 'http://localhost:3001';
    const ollamaBase = process.env.OLLAMA_BASE_URL || 'http://100.94.37.18:11434';
    return [
      { source: '/api/:path*',          destination: `${apiBase}/api/:path*` },
      { source: '/healthz',             destination: `${apiBase}/healthz` },
      { source: '/perplexica/:path*',   destination: `${perplexicaBase}/:path*` },
      { source: '/ollama-proxy/:path*', destination: `${ollamaBase}/:path*` },
    ];
  },
};

export default nextConfig;
