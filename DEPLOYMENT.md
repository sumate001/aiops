# GodEyes Deployment Guide

## Repository Structure

aiops repo มี 3 branches:

- **main**: log-analyzer (A1+A3+AA agents)
  ```
  log-analyzer/
  ├── app/
  ├── tests/
  ├── Dockerfile
  └── requirements.txt
  ```

- **log-ml**: log-ml service (A1 Isolation Forest)
  ```
  log-ml/
  ├── app/
  ├── tests/
  ├── Dockerfile
  └── requirements.txt
  ```

- **godeyes**: orchestration (docker-compose + configs)
  ```
  docker-compose.yml
  perplexica/
  searxng/
  README.md
  ```

## Deploy Locally

```bash
# Clone all 3 branches
git clone https://github.com/sumate001/aiops.git
cd aiops

# Setup local structure
mkdir -p local-deploy
git archive main | tar -x -C local-deploy
git archive log-ml | tar -x -C local-deploy
git archive godeyes | tar -x -C local-deploy

cd local-deploy
docker-compose up -d
```

## Deploy in Production

```bash
# Option 1: Merge into main (requires restructuring)
git checkout main
git merge log-ml --allow-unrelated-histories
git merge godeyes --allow-unrelated-histories
# Resolve conflicts + commit + push

# Option 2: Use separate directories in CI/CD
# Clone branches to /log-analyzer, /log-ml, /godeyes
# Run docker-compose from godeyes/ with -f ../docker-compose.yml
```

## Service Endpoints (after docker-compose up)

- **log-analyzer**: http://localhost:8200 (A1+A3+AA)
- **log-ml**: http://localhost:3050 (Isolation Forest)
- **perplexica-backend**: http://localhost:3001 (A2 search)
- **perplexica-frontend**: http://localhost:3002 (UI)
- **searxng**: http://localhost:4000 (search backend)

## Environment Setup

```bash
# log-analyzer config
cat > log-analyzer/config.yaml <<EOF
log_ml:
  base_url: "http://log-ml:3050"
  enabled: true
perplexica:
  base_url: "http://perplexica-backend:3001"
  enabled: true
EOF

# Start services
docker-compose up -d
docker-compose logs -f log-analyzer
```
