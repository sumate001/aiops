# GodEyes AIOps Platform

Mixture of Agents (MoA) architecture for intelligent log analysis and root-cause synthesis.

## Components

- **A1 Rule + Isolation Forest** (`log-analyzer`) — anomaly detection baseline + statistical ML
- **A2 Perplexica** (via docker-compose) — external knowledge enrichment
- **A3 MiroFish** (`log-analyzer`) — 5-frame multi-perspective analysis
- **AA Synthesizer** (`log-analyzer`) — LLM-as-Judge root-cause synthesis

## Quick Start

```bash
docker-compose up -d
# Endpoints:
# - log-analyzer: http://localhost:8200
# - log-ml: http://localhost:3050
# - perplexica: http://localhost:3002
# - searxng: http://localhost:4000
```

## Architecture

```
docker-compose.yml
├── log-analyzer (A1+A3+AA) :8200
├── log-ml (A1 Isolation Forest) :3050
├── perplexica (A2) :3001
└── searxng (search backend) :4000
```

See `log-analyzer/DEVELOPER.md` and `log-ml/` for detailed setup.
