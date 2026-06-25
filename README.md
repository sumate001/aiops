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
│   ├── ingest.py                # POST /ingest — log ingestion from LogSim
│   ├── config_router.py         # GET/POST /api/config — runtime config management
│   └── results_router.py        # GET /api/results, GET /api/status
└── services/
    ├── baseline_store.py        # SQLite window_stats persistence
    ├── log_ml_client.py         # HTTP client → log-ml :3050
    ├── log_processor.py         # health score, grouping, filtering
    ├── metrics.py               # Prometheus metrics (health, MiroFish, synthesis)
    ├── mirofish.py              # A3 — 5-frame parallel analysis
    ├── ollama.py                # Ollama LLM client
    ├── perplexica_client.py     # A2 — Perplexica search + enrichment
    ├── predictor.py             # Trend analysis + prediction
    ├── result_store.py          # SQLite result store (max 500, auto-pruned)
    └── synthesizer.py           # AA — LLM-as-Judge synthesis

frontend/                        # Next.js UI :3100
├── pages/
│   ├── index.tsx                # Dashboard — pipeline status + recent results
│   ├── results.tsx              # Results viewer (MiroFish frames, AA synthesis)
│   └── settings.tsx             # Settings — config all agents + model dropdowns
└── next.config.mjs              # Proxy rewrites → backend :8200, Ollama, Perplexica
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

### 🚀 One-command deploy (recommended)

ติดตั้ง + start ทุก service ด้วยคำสั่งเดียว — backend, log-ml (A1-IF), Perplexica/Vane,
SearXNG (docker) และ frontend:

```bash
git clone https://github.com/sumate001/aiops.git && cd aiops

# ชี้ไปยัง Ollama ที่มี model gemma4:e4b + nomic-embed-text (default = localhost)
export OLLAMA_BASE_URL=http://100.x.x.x:11434     # remote/Tailscale หรือ localhost

bash deploy.sh            # install (ครั้งแรก) + start ทุกอย่าง → เปิด http://localhost:3002
```

**Prerequisites:** Python 3.11+ (3.14 แนะนำ), **Node ≥ 18 (22 แนะนำ — `nvm use 22`)**, Docker (สำหรับ SearXNG)
deploy.sh จะเช็คให้และหยุดพร้อมบอกวิธีแก้ถ้าขาด

```bash
bash deploy.sh --status   # ดูสถานะทุก service
bash deploy.sh --start    # (re)start เฉย ๆ ข้าม install
bash deploy.sh --stop     # หยุดทั้งหมด
```

logs อยู่ใน `logs/` · pidfiles ใน `.run/` · config อยู่ที่ `config.yaml`
(สร้างจาก `config.yaml.example` อัตโนมัติครั้งแรก — แก้ `ollama.base_url` ตามจริง)

> A2 Perplexica ช้าเมื่อ Ollama รันบน CPU (~10 tok/s) — timeout ตั้งไว้ 480s/host
> `/ingest` ที่มี `callback_url` จะรันแบบ background แล้วตอบ 202 ทันที (ผลส่งกลับทาง callback)

### Local Development (manual / alternative)

```bash
# 1. backend
python3.14 -m pip install -r requirements.txt
cp config.yaml.example config.yaml   # แก้ค่า ollama.base_url, callback_url ฯลฯ
uvicorn app.main:app --port 8200     # http://localhost:8200

# 2. frontend (UI)
cd frontend && npm install && PORT=3002 npm run dev   # http://localhost:3002

# 3. log-ml (Isolation Forest)
uvicorn app.main:app --app-dir log-ml --port 3050     # http://localhost:3050

# 4. Perplexica / Vane (native — Node.js 22+)
git clone https://github.com/sumate001/Vane perplexica-src
cd perplexica-src && npm install && npm run build
cd .next/standalone
PORT=3001 SEARXNG_API_URL=http://localhost:4000 \
  OLLAMA_BASE_URL=http://localhost:11434 DATA_DIR=$(pwd)/../.. node server.js

# 5. SearXNG (docker)
docker run -d --name aiops-searxng -p 4000:8080 \
  -e SEARXNG_SECRET="$(openssl rand -hex 32)" searxng/searxng:latest
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

รับ log entries จาก LogSim (GodEyes JSONL format) แล้วรัน full MoA pipeline และ POST ผลไปที่ `callback_url` ที่ตั้งไว้ใน Settings

### GET /api/config / POST /api/config

ดู/แก้ไข config ทั้งหมดแบบ runtime (ไม่ต้อง restart)

### GET /api/results

ดูผลการวิเคราะห์ที่เก็บไว้ใน SQLite (max 500 รายการ auto-pruned)

```bash
curl http://localhost:8200/api/results?limit=20&tenant_id=store-001
```

### GET /api/status

ตรวจสอบสถานะ agent ทั้งหมด (log-ml, perplexica, ollama)

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
