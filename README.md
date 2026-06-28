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
| **A2** | Perplexica | External knowledge enrichment ผ่าน SearXNG + AI synthesis (chat ผ่าน provider ที่เลือก, embedding บน Transformers ในเครื่อง) |
| **A3** | MiroFish | 5-frame multi-perspective analysis: Security, Database, Network, Hardware, Software |
| **AA** | Synthesizer | LLM-as-Judge weighing A1+A2+A3 → root_cause_chain + confidence + fix_steps |

> 🤖 ทุก stage ที่ใช้ AI (A3 / AA / A2-chat) เลือก **provider** ได้อิสระ — local Ollama หรือ
> free API ภายนอกแบบ OpenAI-compatible (Groq, Cerebras, Gemini, OpenRouter ฯลฯ)
> ตั้งเป็น **ค่า default ตัวเดียว** แล้ว override รายตัวเมื่อจำเป็น ดูหัวข้อ [AI / LLM Providers](#ai--llm-providers)

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
│   ├── analyze.py               # POST /analyze — full MoA pipeline (resolves per-stage LLM)
│   ├── ingest.py                # POST /ingest — log ingestion from LogSim
│   ├── config_router.py         # GET/POST /api/config + /api/llm/providers, /api/llm/models
│   └── results_router.py        # GET /api/results, GET /api/status
└── services/
    ├── baseline_store.py        # SQLite window_stats persistence
    ├── llm.py                   # Unified LLM gateway (Ollama-native + OpenAI-compatible)
    ├── llm_providers.py         # Registry of 13 free / OpenAI-compatible providers
    ├── log_ml_client.py         # HTTP client → log-ml :3050
    ├── log_processor.py         # health score, grouping, filtering
    ├── metrics.py               # Prometheus metrics (health, MiroFish, synthesis)
    ├── mirofish.py              # A3 — 5-frame parallel analysis
    ├── ollama.py                # Ollama native client (used by the gateway)
    ├── perplexica_client.py     # A2 — Perplexica search + provider/embedding wiring
    ├── predictor.py             # Trend analysis + prediction
    ├── result_store.py          # SQLite result store (max 500, auto-pruned)
    └── synthesizer.py           # AA — LLM-as-Judge synthesis

frontend/                        # Next.js UI :3002 (production: next build + next start)
├── pages/
│   ├── index.tsx                # Dashboard — pipeline status + recent results
│   ├── results.tsx              # Results viewer (MiroFish frames, AA synthesis)
│   └── settings.tsx             # Settings — Default AI + per-stage override + model dropdowns
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

# install (ครั้งแรก) + start ทุกอย่าง → เปิด http://localhost:3002
# ⚠️ ใช้ sudo -E เพื่อให้ env (OLLAMA_BASE_URL ฯลฯ) ส่งต่อเข้า sudo ด้วย
sudo -E bash deploy.sh
```
> ถ้าตั้ง provider/Ollama ผ่านหน้า **Settings** อยู่แล้ว ไม่ต้อง export env — รัน `sudo bash deploy.sh` ได้เลย

> 🔑 **ใช้ `sudo` รอบแรก** ถ้ายังไม่มีสิทธิ์ Docker — SearXNG container ต้องคุยกับ Docker daemon
> deploy.sh จะ **เพิ่ม user เข้า group `docker` ให้อัตโนมัติ** (ใช้ user จริงแม้รันผ่าน sudo)
> จากนั้น **logout/login ครั้งเดียว** (หรือ `newgrp docker`) แล้วรอบถัดไปรัน `bash deploy.sh`
> ได้เลยไม่ต้อง sudo (แนะนำ — venv/ไฟล์จะไม่ถูก root ยึด + ไม่ต้องลง Node ซ้ำใน /root)

**Prerequisites:** Python 3.11+ (3.14 แนะนำ), **Node ≥ 18 (22 แนะนำ — `nvm use 22`)**, Docker (สำหรับ SearXNG)
deploy.sh จะเช็คให้และหยุดพร้อมบอกวิธีแก้ถ้าขาด

```bash
sudo bash deploy.sh --status   # ดูสถานะทุก service
sudo bash deploy.sh --start    # (re)start เฉย ๆ ข้าม install
sudo bash deploy.sh --update   # หลัง `git pull`: refresh deps + rebuild frontend + restart
sudo bash deploy.sh --stop     # หยุดทั้งหมด
```

> 🔄 **อัปเดตเครื่องที่ deploy แล้ว**: `git pull` แล้วรัน `bash deploy.sh --update` —
> จะ `pip install` deps ใหม่, rebuild frontend, แล้ว restart ทั้ง stack ให้ (ไม่แตะ
> `perplexica-src/`, `config.yaml`, DB) ถ้าแก้แค่ Python ล้วน ใช้ `--stop && --start` ก็พอ

> ⚠️ ถ้ารันด้วย `sudo` ครั้งแรก แล้วภายหลังรันแบบไม่มี sudo (หรือสลับไปมา) อาจเจอปัญหา
> สิทธิ์ไฟล์ `.venv/`, `logs/`, `.run/`, `frontend/.next/` (root เป็นเจ้าของ) — ให้ใช้คำสั่ง
> เดิมตลอด หรือ `sudo chown -R $USER:$USER .` เพื่อคืนสิทธิ์ก่อนสลับ

logs อยู่ใน `logs/` · pidfiles ใน `.run/` · config อยู่ที่ `config.yaml`
(สร้างจาก `config.yaml.example` อัตโนมัติครั้งแรก — แก้ `ollama.base_url` ตามจริง)

> `/ingest` ที่มี `callback_url` จะรันแบบ background แล้วตอบ 202 ทันที (ผลส่งกลับทาง callback)

#### ⚠️ A2 Perplexica — ข้อกำหนดเรื่องโมเดล
Vane (A2) เรียกโมเดล chat แบบ **strict `json_schema`** *และ* **tool-calling** (web_search)
ในขั้นตอนเดียวกัน — โมเดลต้องทำได้ทั้งคู่ ไม่งั้น request จะ error/ค้างจน timeout แล้ว
ลากให้ผลทั้งก้อนไม่ถูกบันทึก ผลทดสอบบนโมเดล**ฟรี**ของ Groq:

| โมเดล (stage `llm.perplexica`) | ใช้กับ A2 ได้ไหม |
|---|---|
| **`openai/gpt-oss-20b`** | ✅ ผ่านครบ + ได้ web sources จริง (แนะนำ) |
| `openai/gpt-oss-120b` | ⚠️ ผ่าน schema แต่มักไม่ค้น web (0 sources) |
| `meta-llama/llama-4-scout` / `llama-3.3-70b` / `qwen3` | ❌ tool_use_failed หรือไม่รองรับ json_schema |

- ตั้งค่าโมเดล A2 ที่ **Settings → per-stage override (Perplexica)** หรือ `llm.perplexica.model`
- `perplexica.mode` (Vane optimizationMode): default **`speed`** = เสถียร + ได้ sources;
  `balanced`/`quality` ให้คำตอบลึกกว่าแต่บนโมเดลฟรี Groq อาจค้างจาก tool-use loop
- `perplexica.timeout`: default `90s` (speed mode ตอบ ~20s/host)

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

# 5. SearXNG (docker) — ต้องเปิด json format ไม่งั้น Perplexica ได้ 0 sources
docker run -d --name aiops-searxng -p 4000:8080 \
  -e SEARXNG_SECRET="$(openssl rand -hex 32)" searxng/searxng:latest
sleep 4 && docker exec aiops-searxng sh -c \
  "printf '\nsearch:\n  formats:\n    - html\n    - json\n' >> /etc/searxng/settings.yml" \
  && docker restart aiops-searxng
```
> `deploy.sh` ทำขั้นตอน json format นี้ให้อัตโนมัติ — ทำมือเฉพาะตอนรัน SearXNG เอง

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

ตรวจสอบสถานะ agent ทั้งหมด (log-ml, perplexica, llm)

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
  timeout: "480s"           # CPU Ollama ช้า; Groq/Cerebras เร็วกว่ามาก
  enabled: false            # true เมื่อ deploy Perplexica แล้ว
  embedding_model: "nomic-embed-text:latest"   # A2 จะ map ไป Transformers ในเครื่องอัตโนมัติ

# ── AI gateway: default ตัวเดียว + override รายตัว ──
llm:
  enabled: true             # ปิด = rule-based เท่านั้น
  provider: "ollama"        # หรือ groq / cerebras / gemini / openrouter ...
  base_url: "http://localhost:11434"
  model: "gemma4:e4b"
  api_key: null             # OpenAI-compatible providers: ใส่ key หรือ set ผ่าน env
  timeout: "120s"
  temperature: 0.1
  mirofish:    { override: false }   # ใช้ default; เปิด override เพื่อเลือก provider/model เอง
  synthesizer: { override: false }
  perplexica:  { override: false }   # A2 ต้องใช้ model ที่รองรับ json_schema (เช่น groq openai/gpt-oss-120b)
```

## AI / LLM Providers

ทุก stage ที่ใช้ AI เลือก provider ได้ผ่าน **gateway เดียว** (`app/services/llm.py`):
`provider: "ollama"` ใช้ native `/api/generate`; provider อื่นทั้งหมดเป็น **OpenAI-compatible**
(`POST /chat/completions` + Bearer key) รายชื่ออยู่ใน `app/services/llm_providers.py`
(อ้างอิง [awesome-free-llm-apis](https://github.com/mnfst/awesome-free-llm-apis)):

`ollama` (local) · `groq` · `cerebras` · `gemini` · `openrouter` · `mistral` · `cohere` ·
`sambanova` · `nvidia` · `github` · `siliconflow` · `llm7` · `ovhcloud`

### Default + per-stage override
ตั้ง **Default AI** ครั้งเดียว → ทุก stage ใช้ตามนั้น เปิด **override** ของ stage ไหน
(MiroFish / Synthesizer / Perplexica) เพื่อใช้ provider/model ต่างออกไป — ตั้งได้ในหน้า
**Settings → AI (LLM)** หรือใน `config.yaml` (`llm.<stage>.override: true`)

### เลือก provider ยังไง
| ต้องการ | แนะนำ |
|---------|-------|
| เร็ว + ฟรี (ขอ key 1 นาที ไม่ใช้บัตร) | **Groq** / **Cerebras** — 30 RPM |
| ไม่ต้อง key เลย | **OVHcloud** (`Qwen3.5-9B`) — แต่ 2 RPM ช้า เหมาะทดสอบ |
| ออฟไลน์ / ไม่จำกัด | **Ollama** local |

API key ตั้งใน Settings (เก็บใน `config.yaml` แบบ gitignored ไม่เคยส่งกลับ browser) หรือผ่าน
env เช่น `GROQ_API_KEY`, `GEMINI_API_KEY` (ดู `api_key_env` ของแต่ละ provider)

### ข้อควรรู้สำหรับ A2 Perplexica
- **chat** ใช้ provider ที่ resolve ได้; ระบบสร้าง provider ใน Perplexica ให้อัตโนมัติ
  (native `groq`/`gemini` ไม่งั้นใช้ type `openai` ชี้ base_url)
- A2 บังคับ `response_format: json_schema` → ต้องใช้ model ที่รองรับ (เช่น Groq `openai/gpt-oss-120b`)
  ส่วน A3/AA ใช้ model ทั่วไปได้ (เช่น `llama-3.3-70b-versatile`)
- **embedding** รันบน **Transformers** ในเครื่อง (มากับ Perplexica ไม่ต้องลง Ollama)
- `deploy.sh` เปิด SearXNG **json format** อัตโนมัติ (default ปิด → A2 จะได้ 0 sources)

### Endpoints
- `GET /api/llm/providers` — รายชื่อ provider ในรีจิสทรี
- `GET /api/llm/models?provider=&base_url=&api_key=` — list model ของ provider นั้น

## Tech Stack

- **Python 3.11** (required — PyO3/pydantic-core max 3.12)
- **FastAPI** + **uvicorn**
- **scikit-learn** (Isolation Forest)
- **prometheus-client** (metrics)
- **SQLite** (baseline window_stats)
- **LLM gateway** — provider-agnostic (Ollama-native + OpenAI-compatible free APIs)
- **Perplexica + SearXNG** (external search, optional)
- **Prometheus** + **Grafana** (observability)
- **Docker Compose** (orchestration)

## License

Private — sumate001
