FROM node:20-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci --legacy-peer-deps
COPY . .
ENV DOCKER_BUILD=1
ENV API_BASE_URL=http://log-analyzer:8200
ENV PERPLEXICA_BASE_URL=http://vane:3001
ENV OLLAMA_BASE_URL=http://host.docker.internal:11434
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV PORT=3100
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public
EXPOSE 3100
CMD ["node", "server.js"]
