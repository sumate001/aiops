# GodEyes AIOps Platform

Mixture of Agents (MoA) architecture สำหรับวิเคราะห์ log อัจฉริยะ ตรวจจับ anomaly และสังเคราะห์ root cause อัตโนมัติ

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    GodEyes AIOps                        │
│                                                         │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐             │
│  │    A1    │  │    A2    │  │    A3    │             │
│  │ Rule +  │  │Perplexica│  │ MiroFish │             │
│  │Isolation │  │ SearXNG  │  │ 5-Frame  │             │
│  │ Forest   │  │+ AI Syn  │  │ Analysis │             │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘             │
│       │              │              │                   │
│       └──────────────┼──────────────┘                   │
│                      ▼                                  │
│              ┌──────────────┐                           │
│              │      AA      │                           │
│              │  Synthesizer │                           │
│              │ LLM-as-Judge │                           │
│              └──────┬───────┘                           │
│                     ▼                                   │
│           root_cause_chain                              │
│           confidence score                              │
│           fix_steps                                     │
└─────────────────────────────────────────────────────────┘
```

## Agents

| Agent | Component | Description |
|-------|-----------|-------------|
| **A1** | Rule + Isolation Forest | Rule-based anomaly detection + multivariate statistical ML (9 features) |
| **A2** | Perplexica | External knowledge enrichment ผ่าน SearXNG + AI synthesis (quality mode) |
| **A3** | MiroFish | 5-frame multi-perspective analysis: Security, Database, Network, Hardware, Software |
| **AA** | Synthesizer | LLM-as-Judge weighing A1+A2+A3 → root_cause_chain + confidence + fix_steps |

## Repository Structure

```
aiops repo (3 branches)
├── main          → log-analyzer (A1 + A3 + AA agents + /metrics)
├── log-ml        → Isolation Forest ML service (A1)
└── godeyes       → docker-compose + Prometheus + Grafana + Perplexica + SearXNG
```

### main branch (log-analyzer)

```
app/
├── config.py                    # AppConfig (YAML-based)
├── main.py                      # FastAPI entrypoint :8200 + GET /metrics
├── knowledge/
│   └── pos.py                   # POS domain signal extraction
├── models/
│   ├── request.py               # AnalyzeRequest, IngestRequest
│   └── response.py              # HostAnalysis, MiroFishFrame, Synthesis, etc.
├── routers/
│   ├── analyze.py               # POST /analyze — full MoA pipeline
│   └── ingest.py                # POST /ingest — log ingestion from LogSim
└── services/
    ├── baseline_store.py        # SQLite window_stats persistence
    ├── log_ml_client.py         # HTTP client → log-ml :3050
    ├── log_processor.py         # health score, grouping, filtering
    ├── metrics.py               # Prometheus metrics (health, MiroFish, synthesis)
    ├── mirofish.py              # A3 — 5-frame parallel analysis
    ├── ollama.py                # Ollama LLM client
    ├── perplexica_client.py     # A2 — Perplexica search + enrichment
    ├── predictor.py             # Trend analysis + prediction
    └── synthesizer.py           # AA — LLM-as-Judge synthesis
```

### log-ml branch

```
app/
├── main.py                      # FastAPI entrypoint :3050
├── models/schemas.py            # WindowStatInput, AnomalyRequest/Response
├── routers/
│   ├── anomalies.py             # POST /anomalies — IF scoring
│   ├── train.py                 # POST /train — retrain model
│   └── models_router.py         # GET /models — list trained models
└── services/
    ├── features.py              # 9-feature extraction + rule-based fallback
    └── forest.py                # Isolation Forest train/score/persist
```

## Quick Start

### Local Development

```bash
# 1. log-analyzer
cd log-analyzer
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config.yaml.example config.yaml
uvicorn app.main:app --port 8200    # http://localhost:8200

# 2. log-ml
cd log-ml
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --port 3050    # http://localhost:3050

# 3. Prometheus (brew)
brew install prometheus
prometheus --config.file=prometheus/prometheus.yml --web.listen-address=":9090"

# 4. Grafana (brew)
brew install grafana && brew services start grafana
# Import grafana/provisioning/dashboards/godeyes.json
# http://localhost:3000 (admin/admin)
```

### Docker Compose (Full Stack)

```bash
git clone https://github.com/sumate001/aiops.git
cd aiops
git checkout godeyes
docker-compose up -d

# Endpoints:
#   log-analyzer    → http://localhost:8200
#   log-ml          → http://localhost:3050
#   perplexica UI   → http://localhost:3002
#   searxng          → http://localhost:4000
#   prometheus       → http://localhost:9090
#   grafana          → http://localhost:3003 (admin/godeyes)
```

## E2E Flow (LogSim → log-analyzer → Prometheus → Grafana)

```
LogSim (output_dest=log_analyzer)
    │
    │  POST /ingest  (GodEyes JSONL entries)
    ▼
log-analyzer :8200
    │
    ├─ filter + group by host
    ├─ A1: save window_stats (SQLite)
    ├─ A1: log-ml Isolation Forest score
    ├─ A3: MiroFish 5-frame analysis
    ├─ AA: Synthesizer → root_cause_chain + fix_steps
    ├─ A2: Perplexica enrichment (optional)
    └─ update /metrics (Prometheus counters + gauges)
         │
         │  scrape every 15s
         ▼
    Prometheus :9090
         │
         │  query
         ▼
    Grafana :3000/3003
    (GodEyes AIOps dashboard)
```

### ทดสอบ E2E

```bash
# Start LogSim backend
cd /path/to/logsim/backend
uvicorn main:app --port 8071

# ส่ง scenario mysql_cascade
curl -X POST http://localhost:8071/api/simulate \
  -H 'Content-Type: application/json' \
  -d '{
    "scenario_id": "mysql_cascade",
    "output_dest": "log_analyzer",
    "log_analyzer_url": "http://localhost:8200",
    "log_analyzer_tenant_id": "store-001",
    "log_analyzer_asset_id": "pos-cluster-01",
    "speed": 1.0
  }'

# ดู metrics
curl http://localhost:8200/metrics | grep godeyes_

# ดู Grafana dashboard
open http://localhost:3000/d/godeyes-aiops/godeyes-aiops
```

## API

### POST /analyze

Full MoA pipeline: ingest logs → A1 anomaly detection → A3 MiroFish → AA Synthesis

```bash
curl -X POST http://localhost:8200/analyze \
  -H 'Content-Type: application/json' \
  -d '{
    "tenant_id": "store-001",
    "window": {
      "from": "2026-06-22T09:00:00Z",
      "to": "2026-06-22T09:10:00Z"
    },
    "entries": [
      {
        "host": "pos-01",
        "hostname": "pos-01",
        "service": "pos",
        "severity_text": "error",
        "severity_number": 17,
        "msg": "deadlock detected: transaction 2847",
        "time": "2026-06-22T09:03:00Z"
      }
    ]
  }'
```

### Response (abbreviated)

```json
{
  "health_score": 0.0,
  "status": "critical",
  "hosts": [
    {
      "host": "pos-01",
      "health_score": 0.0,
      "status": "critical",
      "mirofish": [
        {
          "frame": "Database",
          "lens": "dba",
          "relevance": 0.7,
          "signal_hits": 2,
          "top_keywords": ["deadlock", "timeout"]
        }
      ],
      "synthesis": {
        "root_cause_chain": [
          "[Database] Primary domain: Dba — relevance 70%"
        ],
        "confidence": 0.42,
        "fix_steps": [
          "Check for long-running queries with SHOW PROCESSLIST",
          "Kill deadlocked transactions and review lock contention"
        ],
        "method": "rule",
        "top_frame": "Database"
      }
    }
  ]
}
```

### POST /ingest

รับ log entries จาก LogSim (GodEyes JSONL format)

### GET /metrics

Prometheus metrics endpoint — scraped ทุก 15 วินาที

### GET /healthz

Health check

## Prometheus Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `godeyes_host_health_score` | Gauge | Health score 0-100 per host |
| `godeyes_host_status` | Gauge | OK=1, WARNING=2, CRITICAL=3 |
| `godeyes_host_error_count` | Gauge | Error log count per window |
| `godeyes_host_warn_count` | Gauge | Warning log count per window |
| `godeyes_host_anomaly_count` | Gauge | Anomaly count per host |
| `godeyes_host_anomaly_score` | Gauge | Max anomaly score (0-1) |
| `godeyes_mirofish_frame_relevance` | Gauge | MiroFish frame relevance per host |
| `godeyes_synthesis_confidence` | Gauge | AA Synthesizer confidence (0-1) |
| `godeyes_analyze_requests_total` | Counter | Total /analyze requests |
| `godeyes_analyze_duration_seconds` | Histogram | Request duration |

## Grafana Dashboard

Dashboard: **GodEyes AIOps** (uid: `godeyes-aiops`)

Panels:
- **Overall Health Score** — gauge 0-100 (red/yellow/green)
- **Host Health Score** — timeseries per host
- **Host Status** — stat OK/WARNING/CRITICAL
- **Error/Warn Count** — timeseries per host
- **MiroFish Frame Relevance** — timeseries (Security/DB/Network/HW/SW)
- **AA Synthesis Confidence** — timeseries per host + top_frame
- **Anomaly Count** — timeseries per host
- **Analyze Requests/min** — request rate timeseries

## Configuration

```yaml
# config.yaml
log_ml:
  base_url: "http://localhost:3050"
  timeout: "10s"
  enabled: true

perplexica:
  base_url: "http://localhost:3001"
  timeout: "30s"
  enabled: false          # true เมื่อ deploy Perplexica แล้ว

ollama:
  base_url: "http://localhost:11434"
  model: "qwen2.5:14b"
  timeout: "120s"
  temperature: 0.1
```

## Tech Stack

- **Python 3.11** (required — PyO3/pydantic-core max 3.12)
- **FastAPI** + **uvicorn**
- **scikit-learn** (Isolation Forest)
- **prometheus-client** (metrics)
- **SQLite** (baseline window_stats)
- **Ollama** (LLM inference, optional)
- **Perplexica + SearXNG** (external search, optional)
- **Prometheus** + **Grafana** (observability)
- **Docker Compose** (orchestration)

## License

Private — sumate001
