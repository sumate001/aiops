# Log-Analyzer — Developer Guide

> **GodEye AIOps Platform** · Microservice สำหรับวิเคราะห์ log จาก POS ระบบ  
> Last updated: 2026-07-02

---

## สารบัญ

1. [ภาพรวมระบบ](#1-ภาพรวมระบบ)
2. [โครงสร้างโปรเจกต์](#2-โครงสร้างโปรเจกต์)
3. [Setup & Run](#3-setup--run)
4. [Configuration](#4-configuration)
5. [API Endpoints](#5-api-endpoints)
6. [Data Flow](#6-data-flow)
7. [Knowledge Base (POS)](#7-knowledge-base-pos)
8. [Predictive Engine](#8-predictive-engine)
9. [Baseline Store (SQLite)](#9-baseline-store-sqlite)
10. [GodEyes Adapter](#10-godeyes-adapter)
11. [Testing](#11-testing)
12. [สิ่งที่ยังไม่ได้ทำ (Roadmap)](#12-สิ่งที่ยังไม่ได้ทำ-roadmap)
13. [วิธีขยายระบบ](#13-วิธีขยายระบบ)

---

## 1. ภาพรวมระบบ

```
GodEyes Platform
      │
      │ POST /ingest  (JSONL หรือ JSON)
      ▼
┌─────────────────────────────────────────────────────┐
│                   log-analyzer                      │
│                                                     │
│  GodEyes Adapter → Filter → Group by host           │
│       ↓                                             │
│  [Per host — concurrent]                            │
│    Health Score  ←── Log Processor                  │
│    Signal Extract ←── POS Knowledge Base            │
│    Ollama AI      ←── POS Context + top errors      │
│    Trend Analysis ←── SQLite Baseline Store         │
│    Fingerprint    ←── POS Failure Fingerprints      │
│    Noise Reduce   ←── POS Noise Patterns            │
│       ↓                                             │
│  JSON Response (AnalyzeResponse)                    │
└─────────────────────────────────────────────────────┘
      │
      ▼
  GodEyes / Dashboard / Export File
```

### Stack
| Component | Technology |
|-----------|-----------|
| Web framework | FastAPI + Uvicorn (Python 3.12) |
| Schema validation | Pydantic v2 |
| LLM | gateway provider-agnostic (`app/services/llm.py`) — Ollama native หรือ OpenAI-compatible (Groq ฯลฯ) เลือกต่อ stage ได้ |
| Baseline DB | SQLite (file: `log_analyzer.db`) |
| Frontend dashboard | Next.js (`frontend/`, port 3002) |
| Tests | pytest (56 tests) |

---

## 2. โครงสร้างโปรเจกต์

```
log-analyzer/
├── app/
│   ├── main.py                  # FastAPI app, lifespan, mount static
│   ├── config.py                # โหลด config.yaml, SEVERITY_THRESHOLD
│   │
│   ├── models/
│   │   ├── request.py           # AnalyzeRequest, LogEntry, TimeWindow
│   │   ├── response.py          # AnalyzeResponse, HostAnalysis, TrendInfo, PredictionInfo
│   │   └── ingest.py            # GodEyesIngestRequest (input format ดิบ)
│   │
│   ├── routers/
│   │   ├── analyze.py           # POST /analyze — pipeline หลัก
│   │   ├── ingest.py            # POST /ingest — รับ GodEyes format แล้ว forward
│   │   └── health.py            # GET /healthz — probe Ollama + aiops-ml
│   │
│   ├── services/
│   │   ├── log_processor.py     # filter, group, health score (capped deductions), top errors (warn fallback), summary
│   │   ├── godeyes_adapter.py   # แปลง GodEyes format → canonical LogEntry / MetricSample
│   │   ├── metric_analyzer.py   # metric threshold check + unit-mismatch guard → AnomalyScore
│   │   ├── mirofish.py          # A3 — 5-frame analysis + per-frame LLM insight
│   │   ├── synthesizer.py       # AA — rule pass + LLM judge (root_cause_chain/reasoning/fix_steps)
│   │   ├── perplexica_client.py # A2 — search + result cache 6h + cooldown 60s
│   │   ├── llm.py               # unified LLM gateway (Ollama native + OpenAI-compatible)
│   │   ├── ollama.py            # Ollama native client (ใช้ผ่าน gateway)
│   │   ├── aiops_ml.py          # optional ML service client (graceful degrade)
│   │   ├── log_ml_client.py     # Isolation Forest service client (:3050)
│   │   ├── baseline_store.py    # SQLite persistence (window stats, baseline)
│   │   ├── result_store.py      # SQLite ผลวิเคราะห์ (max 500, auto-pruned)
│   │   └── predictor.py         # trend analysis + POS fingerprint matching
│   │
│   ├── knowledge/
│   │   └── pos.py               # POS Knowledge Base ทั้งหมด (แก้ไขตรงนี้)
│   │
│   └── static/
│       └── index.html           # Dark dashboard (Tailwind CDN)
│
├── tests/
│   ├── test_log_processor.py
│   ├── test_request_validation.py
│   ├── test_ollama.py
│   └── test_godeyes_adapter.py
│
├── config.yaml                  # Runtime config (ไม่ commit ถ้ามี secret)
├── config.yaml.example          # Template
├── requirements.txt
├── Dockerfile
└── Makefile
```

---

## 3. Setup & Run

### Prerequisites
- Python 3.12+
- Ollama ที่ accessible จาก machine นี้

### Local development

```bash
cd log-analyzer

# 1. สร้าง virtual env
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 2. ติดตั้ง dependencies
pip install -r requirements.txt

# 3. คัดลอก config
cp config.yaml.example config.yaml
# แล้วแก้ไข ollama.base_url ให้ชี้ไปที่ Ollama server

# 4. รัน server (port 8000 สำหรับ dev, 8200 สำหรับ production)
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# 5. เปิด dashboard
open http://localhost:8000

# 6. เปิด Swagger UI
open http://localhost:8000/docs
```

### Docker

```bash
docker build -t log-analyzer .
docker run -p 8200:8200 \
  -e LA_DB_PATH=/data/log_analyzer.db \
  -v $(pwd)/config.yaml:/app/config.yaml \
  -v log-analyzer-data:/data \
  log-analyzer
```

> **Environment variable**: `LA_DB_PATH` กำหนด path ของ SQLite file (default: `log_analyzer.db` ใน working dir)

---

## 4. Configuration

ไฟล์ `config.yaml` (อ้างอิงจาก `config.yaml.example`)

```yaml
ollama:
  base_url: "http://100.94.37.18:11434"   # URL ของ Ollama server
  model: "qwen3.6:35b"                    # ชื่อ model ที่ pull ไว้แล้ว
  timeout: "120s"                          # timeout ต่อ request
  temperature: 0.1                         # ต่ำ = factual, สูง = creative

aiops_ml:
  enabled: false                           # true = เปิด ML service (optional)
  base_url: "http://aiops-ml:8100"
  timeout: "60s"

analysis:
  severity_filter: "warn"                  # กรองเฉพาะ warn ขึ้นไป
  max_log_entries: 500                     # cap per request ป้องกัน OOM
```

### Severity mapping (OTEL standard)

| Level | severity_number |
|-------|----------------|
| trace | 1–4 |
| debug | 5–8 |
| info | 9–12 |
| **warn** | **13–16** |
| **error** | **17–20** |
| **fatal** | **21–24** |

`severity_filter: "warn"` หมายถึงรับ entry ที่ `severity_number >= 13`

---

## 5. API Endpoints

### `POST /ingest` — รับ log จาก GodEyes (main entry point)

รองรับ 2 format:

**JSON body:**
```bash
curl -X POST http://localhost:8000/ingest \
  -H "Content-Type: application/json" \
  -d '{"entries": [<GodEyes log objects>]}'
```

**JSONL stream:**
```bash
curl -X POST http://localhost:8000/ingest \
  -H "Content-Type: application/x-ndjson" \
  --data-binary @logs.jsonl
```

Optional fields ใน JSON body:
```json
{
  "entries": [...],
  "tenant_id": "bigc-store-01",
  "request_id": "uuid-optional",
  "window_from": "2026-05-20T00:00:00Z",
  "window_to":   "2026-05-20T12:00:00Z"
}
```
> ถ้าไม่ส่ง `window_from`/`window_to` — ระบบ derive จาก `min`/`max` timestamp ของ entries อัตโนมัติ

---

### `POST /analyze` — canonical format (internal / testing)

```json
{
  "tenant_id": "bigc",
  "window": {
    "from": "2026-05-20T00:00:00Z",
    "to":   "2026-05-20T12:00:00Z"
  },
  "entries": [
    {
      "time": "2026-05-20T02:22:08Z",
      "host": "POCPOS2008R2.bigc.co.th",
      "service": "MSSQL",
      "severity_text": "error",
      "severity_number": 17,
      "msg": "Login failed for user 'sa'.",
      "service_profile": "db_mssql",
      "criticality": "gold"
    }
  ]
}
```

### `GET /healthz` — probe

```json
{
  "status": "ok",
  "ollama": "reachable",
  "ollama_model": "qwen3.6:35b",
  "aiops_ml": "disabled"
}
```

### `GET /` — Dashboard (HTML)
### `GET /docs` — Swagger UI

---

## 6. Data Flow

```
POST /ingest
    │
    ├─ (JSONL) parse lines → raw_entries[]
    └─ (JSON)  GodEyesIngestRequest → raw_entries[]
         │
         ▼
    godeyes_adapter.build_analyze_request()
      - transform_entry() × N  →  canonical LogEntry dicts
      - derive_window()         →  window_from, window_to
         │
         ▼
    AnalyzeRequest (validated by Pydantic)
         │
         ▼
    analyze() router
      1. filter_entries()        → กรอง severity + cap 500 (เก็บยอด pre-filter ต่อ host ไว้เป็นตัวหาร health)
      2. group_by_host()         → dict[host → entries[]] (+ metric_analyzer.group_by_host สำหรับ metrics)
      3. รันเป็น phase (ทุก host ต่อ phase):
         │
         ▼
    Phase 1  A1 (parallel)
      ├─ compute_host_health_score()   → 0–100 (ตัวหาร = pre-filter total; cap: warn -35, error -70, anomaly -30)
      ├─ compute_top_errors()          → top 5 error msgs (ไม่มี error → fallback เป็น warn msgs)
      ├─ metric_analyzer.evaluate_host() → metric threshold anomalies (มี unit-mismatch guard)
      ├─ extract_signals_from_messages() → 7 signal types
      ├─ log-ml Isolation Forest score → anomaly + ปรับ health
      ├─ save_window_stat()            → บันทึก SQLite
      ├─ analyze_trend()               → TrendInfo (slope, anomaly types)
      └─ generate_prediction()         → PredictionInfo (risk, fingerprint, ETA)
    Phase 2  A3 MiroFish (parallel)    → 5 frames + per-frame LLM insight
    Phase 3  AA rule pass (parallel, no LLM) → rule chain + top_frame (ใช้สร้าง A2 query)
    Phase 4  A2 Perplexica (sequential) → web research (cache 6h, cooldown 60s; ข้ามถ้าไม่มีหลักฐานให้ค้น)
    Phase 5  AA LLM judge (parallel)   → เห็น top errors + insights + ค่า metric + rule chain + ผล A2
                                        → root_cause_chain + confidence + fix_steps + reasoning
         │
         ▼
    AnalyzeResponse (JSON) — summary เป็น plain text บรรทัดเดียว (คั่น " | ", ล้าง markdown)
```

---

## 7. Knowledge Base (POS)

ไฟล์: `app/knowledge/pos.py` — **แก้ไขที่นี่เมื่อต้องการเพิ่ม/ปรับ knowledge**

### 7.1 Failure Fingerprints

Pattern ที่เห็นใน log **ก่อนเกิด incident จริง** 15–120 นาที

```python
POS_FAILURE_FINGERPRINTS = [
    {
        "name": "Auth Failure Cascade",
        "lead_time_minutes": 45,           # ประวัติ: incident เกิดหลัง pattern นี้ ~45 นาที
        "description": "...",
        "required_signals": ["auth_fail", "crash"],   # ต้องมีทั้งคู่
        "supporting_signals": ["db_err"],              # เสริม confidence
        "multi_signal_threshold": 2,
    },
    # ... อีก 6 fingerprints
]
```

**Fingerprints ปัจจุบัน:**

| Fingerprint | Required Signals | Lead Time |
|-------------|-----------------|-----------|
| Database Overload | db_err | 45 min |
| Auth Failure Cascade | auth_fail + crash | 45 min |
| Network Failure | network_err | 30 min |
| Memory Leak / Resource Exhaustion | app_crash | 120 min |
| Payment Processing Failure | payment_fail | 20 min |
| Hardware / Peripheral Failure | hardware_err | 15 min |
| Service Crash Loop | crash | 30 min |

### 7.2 Signal Patterns

Keyword → signal type (จับจาก log message ด้วย substring match)

```python
POS_SIGNAL_PATTERNS = {
    "auth_fail":    ["login failed", "authentication failed", "access denied", ...],
    "crash":        ["terminated unexpectedly", "crashed", "fatal error", ...],
    "db_err":       ["query timeout", "too many connections", "deadlock", ...],
    "network_err":  ["connection timeout", "lost connection", "packet loss", ...],
    "payment_fail": ["payment gateway", "transaction failed", "payment declined", ...],
    "hardware_err": ["printer offline", "barcode scanner", "cash drawer", ...],
    "app_crash":    ["out of memory", "gc pause", "watchdog triggered", ...],
}
```

### 7.3 Noise Patterns

สิ่งที่ดูเหมือน anomaly แต่จริงๆ เป็น routine — ลด risk score โดยอัตโนมัติ

```python
POS_NOISE_PATTERNS = [
    {"name": "End-of-day Batch",   "hours": [22,23,0,1,2,3,4], "risk_reduction": 20},
    {"name": "Peak Hour Traffic",  "hours": [11, 12, 13],       "risk_reduction": 15},
    {"name": "Month-end DB Load",  "day_of_month": [28,29,30,31,1], "risk_reduction": 10},
    {"name": "Post-update Warmup", "keywords": ["warm up", "initializing"], "risk_reduction": 15},
]
```

### 7.4 Ollama Context (POS_OLLAMA_CONTEXT)

String ที่ prepend เข้า prompt ก่อนทุก Ollama request เพื่อให้ LLM เข้าใจ domain POS ได้ถูกต้อง รวมถึง PMPOS-AGENT specific knowledge

---

## 8. Predictive Engine

ไฟล์: `app/services/predictor.py`

### `analyze_trend(host)` → TrendInfo

1. ดึง window stats 15 อันล่าสุดจาก SQLite
2. คำนวณ **linear regression slope** ของ error_rate ตามเวลา
3. ตรวจจับ anomaly types:
   - **spike** — window ล่าสุด > 2× ของ window ก่อนหน้า
   - **drift** — slope สูงขึ้นต่อเนื่อง ≥5 windows
   - **pattern** — ≥3 signal types active พร้อมกัน
   - **baseline_deviation** — Z-score > 3σ เทียบกับ baseline ของชั่วโมงเดียวกัน

### `generate_prediction(...)` → PredictionInfo

```
Risk score = 0
  + 35  if health < 40 (critical)
  + 15  if health < 70 (degraded)
  + 25  if trend rising
  + 15  if spike anomaly
  + 20  if drift anomaly
  + 25  if pattern anomaly
  + 10  if baseline_deviation
  + 10  if error_rate > 50%
  + 20  if fingerprint matched
  − noise_reduction (0–20 จาก noise pattern)
  → clamp 0–100
```

**Risk levels:**
- `critical` ≥ 70
- `high` ≥ 45
- `medium` ≥ 20
- `low` < 20

**Confidence formula:**
```
confidence = (risk/100 × 0.63)
           + (windows_analyzed/10 × 0.20)  # max 0.20
           + 0.10 if pattern anomaly
           + 0.12 if fingerprint matched
           − 0.10 if noise suppressed
           + 0.05 (base)
→ clamp 0.0–0.95
```

---

## 9. Baseline Store (SQLite)

ไฟล์: `app/services/baseline_store.py`

ตาราง `window_stats` — บันทึกทุกครั้งที่วิเคราะห์

| Column | Type | หมายเหตุ |
|--------|------|---------|
| host | TEXT | hostname |
| tenant_id | TEXT | |
| window_from/to | TEXT | ISO-8601 |
| entry_count | INT | จำนวน log entries |
| error_count | INT | |
| warn_count | INT | |
| error_rate | REAL | error_count / entry_count |
| health_score | REAL | 0–100 |
| top_errors | TEXT | JSON array of top 5 messages |
| crash_count | INT | |
| auth_fail_count | INT | |
| payment_fail_count | INT | |
| network_err_count | INT | |
| db_err_count | INT | |
| hardware_err_count | INT | |
| app_crash_count | INT | |

**การ migrate column:** `init_db()` ใช้ `ALTER TABLE ... ADD COLUMN` พร้อม exception handling (ถ้า column มีอยู่แล้วจะ ignore) ดังนั้นปลอดภัยที่จะเพิ่ม column ใหม่ได้เสมอ

**`get_same_hour_baseline(host, hour, day_type)`** — คำนวณ avg/variance ของ error_rate สำหรับ host นั้นๆ ในชั่วโมงเดียวกัน (weekday/weekend แยกกัน) เพื่อใช้เป็น Z-score baseline

> **หมายเหตุ:** ต้องมี ≥3 samples (การวิเคราะห์ ≥3 ครั้ง) ถึงจะได้ baseline ที่มีความหมาย Trend จะยังแสดง `"unknown"` จนกว่าจะมี ≥3 windows

---

## 10. GodEyes Adapter

ไฟล์: `app/services/godeyes_adapter.py`

แปลง GodEyes export format → canonical `LogEntry`:

| GodEyes field | → | Canonical field | หมายเหตุ |
|---------------|---|----------------|---------|
| `_time` | → | `time` | fallback: `time`, `timestamp`, `EventReceivedTime` |
| `message` / `_msg` | → | `msg` | prefer `message`; strip syslog header จาก `_msg` |
| `host` / `hostname` | → | `host` | fallback ถ้า host = `"?"` หรือว่าง |
| `severity_text` (syslog) | → | `severity_text` (OTEL) | `"err"→"error"`, `"warning"→"warn"`, `"notice"→"info"` |
| `severity_number` (string) | → | `severity_number` (int) | coerce + fallback ตาม severity_text |
| `structured_data.*` | → | `fields.*` | flatten และ strip prefix `structured_data.NXLOG@14506.` |

---

## 11. Testing

```bash
# รันทุก tests
python -m pytest tests/ -v

# รันเฉพาะ module
python -m pytest tests/test_godeyes_adapter.py -v
python -m pytest tests/test_log_processor.py -v

# รันพร้อม coverage
python -m pytest tests/ --cov=app --cov-report=term-missing
```

**Test coverage ปัจจุบัน:** 56 tests

| File | Tests |
|------|-------|
| test_godeyes_adapter.py | 30 |
| test_log_processor.py | 15 |
| test_request_validation.py | 6 |
| test_ollama.py | 4 |
| test_analyze_phase1.py | 1 |

> **สำคัญ:** เมื่อเพิ่ม feature ใหม่ใน `pos.py` หรือ `predictor.py` ให้เพิ่ม test ใน `tests/` ด้วยเสมอ

---

## 12. สิ่งที่ยังไม่ได้ทำ (Roadmap)

### Priority สูง

| Feature | คำอธิบาย | ไฟล์ที่เกี่ยวข้อง |
|---------|---------|---------------|
| **Multi-host support** | ตอนนี้ test file มีแค่ 1 host — ต้องทดสอบกับ log ที่มีหลาย host | `analyze.py`, `log_processor.py` |
| **Webhook callback** | เพิ่ม `callback_url` field ใน IngestRequest แล้ว POST ผลกลับ async | `routers/ingest.py` |
| **tenant_id isolation** | ตอนนี้ baseline ไม่แยก tenant — ถ้ามีหลาย tenant ควร partition SQLite ด้วย tenant | `baseline_store.py` |
| **service_profile + criticality** | GodEyes export ปัจจุบันไม่ส่ง field เหล่านี้ — ต้องทำ mapping จาก hostname pattern | `godeyes_adapter.py` |

### Priority ปานกลาง

| Feature | คำอธิบาย |
|---------|---------|
| **Alert routing** | ส่ง alert ออก LINE Notify / PagerDuty เมื่อ risk = critical |
| **Dashboard real-time** | เปลี่ยน dashboard ให้ poll `/analyze` ด้วย SSE หรือ WebSocket |
| **Retention policy** | ลบ window_stats ที่เก่ากว่า N วัน เพื่อไม่ให้ DB โต |
| **PDF/Excel export** | Export ผลวิเคราะห์เป็น PDF report หรือ Excel |
| **aiops-ml integration** | ตอนนี้ `aiops_ml.enabled: false` — เปิดเมื่อ ML service พร้อม |

### Priority ต่ำ

| Feature | คำอธิบาย |
|---------|---------|
| **Auth/API Key** | ป้องกัน endpoint ด้วย API Key สำหรับ production |
| **Prometheus metrics** | Export health scores เป็น Prometheus gauge |
| **Multi-model support** | ให้เลือก Ollama model ต่อ tenant ได้ |

---

## 13. วิธีขยายระบบ

### เพิ่ม Failure Fingerprint ใหม่

แก้ไขใน `app/knowledge/pos.py`:

```python
POS_FAILURE_FINGERPRINTS.append({
    "name": "ชื่อ Scenario",
    "lead_time_minutes": 30,          # นาทีก่อน incident จากประวัติ
    "description": "อธิบาย pattern",
    "required_signals": ["signal_x"], # ต้องมีทุกตัวถึงจะ match
    "supporting_signals": ["signal_y"], # ถ้ามีด้วยจะ score สูงขึ้น
    "multi_signal_threshold": 1,
})
```

> Signal types ที่มี: `crash`, `auth_fail`, `db_err`, `network_err`, `payment_fail`, `hardware_err`, `app_crash`

### เพิ่ม Signal Pattern ใหม่

```python
# เพิ่ม keyword ใน signal type ที่มีอยู่
POS_SIGNAL_PATTERNS["db_err"].append("new db error keyword")

# หรือเพิ่ม signal type ใหม่ทั้งหมด
POS_SIGNAL_PATTERNS["backup_fail"] = ["backup failed", "backup timeout"]
```

> ⚠️ ถ้าเพิ่ม signal type ใหม่ ต้องเพิ่ม column ใน `WindowStat` dataclass และ `save_window_stat()` ด้วย

### เพิ่ม Noise Pattern ใหม่

```python
POS_NOISE_PATTERNS.append({
    "name": "ชื่อ Pattern",
    "hours": [8, 9],                  # ถ้า trigger เฉพาะบางชั่วโมง
    "day_of_month": [1],              # ถ้า trigger เฉพาะบางวัน
    "keywords": ["keyword1", "keyword2"],
    "reason": "คำอธิบายให้ ops team เข้าใจ",
    "risk_reduction": 15,             # หักออกจาก risk score (0–30)
})
```

### เปลี่ยน Ollama Model

แก้ `config.yaml`:
```yaml
ollama:
  model: "llama3.1:8b"   # หรือ model อื่นที่ pull ไว้แล้ว
```

แล้ว restart server — ไม่ต้องแก้ code

### เพิ่ม Endpoint ใหม่

```python
# สร้างไฟล์ app/routers/my_feature.py
from fastapi import APIRouter
router = APIRouter()

@router.get("/my-endpoint")
async def my_endpoint():
    return {"hello": "world"}

# ลงทะเบียนใน app/main.py
from app.routers import my_feature
app.include_router(my_feature.router)
```

---

## ข้อสังเกตสำคัญสำหรับ Dev

1. **Ollama timeout ยาว** — model `qwen3.6:35b` ใช้เวลา 30–90 วินาที ต่อ request บน hardware ปัจจุบัน อย่าลด timeout ต่ำกว่า 120s

2. **First-run trend = unknown** — ต้องวิเคราะห์ ≥3 ครั้ง ถึงจะมี baseline trend ที่มีความหมาย ปกติสำหรับ new host

3. **max_log_entries: 500** — GodEyes export มี 34,000+ entries แต่ระบบ cap ที่ 500 เพื่อป้องกัน OOM และ Ollama prompt ยาวเกิน ถ้าต้องการวิเคราะห์ครบต้องทำ pagination หรือ sampling strategy

4. **SQLite ไม่ scale multi-process** — ถ้า deploy หลาย replica ต้องเปลี่ยนเป็น PostgreSQL หรือใช้ shared volume

5. **GodEyes host field "?"** — บาง entry มี `host = "?"` adapter จะ fallback ไปใช้ `hostname` field อัตโนมัติ

6. **Syslog severity labels** — GodEyes ส่งมาเป็น `"err"`, `"warning"`, `"notice"` ไม่ใช่ OTEL standard — adapter จัดการเรื่องนี้ใน `godeyes_adapter.py` แล้ว

---

*สร้างโดย Claude — สำหรับ GodEye AIOps Platform · log-analyzer v1.0*
