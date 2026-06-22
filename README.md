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
│            root_cause_chain                             │
│            confidence score                             │
│            fix_steps                                    │
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
├── main          → log-analyzer (A1 + A3 + AA agents)
├── log-ml        → Isolation Forest ML service (A1)
└── godeyes       → docker-compose + Perplexica + SearXNG configs
```

### main branch (log-analyzer)

```
app/
├── config.py                    # AppConfig (YAML-based)
├── main.py                      # FastAPI entrypoint :8200
├── knowledge/
│   └── pos.py                   # POS domain signal extraction
├── models/
│   ├── request.py               # AnalyzeRequest, IngestRequest
│   └── response.py              # HostAnalysis, MiroFishFrame, Synthesis, etc.
├── routers/
│   ├── analyze.py               # POST /analyze — full MoA pipeline
│   └── ingest.py                # POST /ingest — log ingestion
└── services/
    ├── baseline_store.py        # SQLite window_stats persistence
    ├── log_ml_client.py         # HTTP client → log-ml :3050
    ├── log_processor.py         # health score, grouping, filtering
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
cp config.yaml.example config.yaml   # แก้ค่าตาม environment
make run                              # http://localhost:8200

# 2. log-ml
cd log-ml
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --port 3050      # http://localhost:3050
```

### Docker Compose (Full Stack)

```bash
git clone https://github.com/sumate001/aiops.git
cd aiops

# Checkout godeyes branch for orchestration files
git checkout godeyes

# Start all services
docker-compose up -d

# Endpoints:
#   log-analyzer    → http://localhost:8200
#   log-ml          → http://localhost:3050
#   perplexica UI   → http://localhost:3002
#   searxng          → http://localhost:4000
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
      "anomalies": [...],
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
      },
      "enrichment": null
    }
  ]
}
```

### POST /ingest

Ingest raw logs (used by LogSim)

```bash
curl -X POST http://localhost:8200/ingest \
  -H 'Content-Type: application/json' \
  -d '{
    "entries": [...],
    "window_from": "2026-06-22T09:00:00Z",
    "window_to": "2026-06-22T09:10:00Z"
  }'
```

### GET /healthz

Health check

```bash
curl http://localhost:8200/healthz
```

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

## MoA Data Flow

```
Log entries
    │
    ▼
┌─────────────────┐
│  /analyze       │
│  filter + group │
└────────┬────────┘
         │
    ┌────┴────┐
    ▼         ▼
┌────────┐ ┌────────────┐
│ Ollama │ │ aiops-ml   │
│ explain│ │ /predict   │
└───┬────┘ └─────┬──────┘
    │            │
    ▼            ▼
┌─────────────────────┐
│  window_stats save  │  ← SQLite baseline
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│  A1: log-ml :3050   │  ← Isolation Forest scoring
│  POST /anomalies    │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│  A3: MiroFish       │  ← 5-frame parallel analysis
│  Security/DB/Net/   │
│  HW/SW              │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│  AA: Synthesizer    │  ← LLM-as-Judge
│  root_cause_chain   │
│  confidence + fix   │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│  A2: Perplexica     │  ← External enrichment (optional)
│  SearXNG + AI       │
└──────────┬──────────┘
           │
           ▼
     HostAnalysis
     (full response)
```

## Tech Stack

- **Python 3.11** (required — PyO3/pydantic-core max 3.12)
- **FastAPI** + **uvicorn**
- **scikit-learn** (Isolation Forest)
- **SQLite** (baseline window_stats)
- **Ollama** (LLM inference)
- **Perplexica + SearXNG** (external search)
- **Docker Compose** (orchestration)

## License

Private — sumate001
