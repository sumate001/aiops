# log-analyzer — Implementation Spec

> **สำหรับ Claude Code**: อ่าน document นี้ทั้งหมดก่อนเขียน code ทุกบรรทัด นี่คือ new microservice ที่จะ plug เข้าไปใน GodEye AIOps Platform

---

## 1. Overview

`log-analyzer` คือ Python/FastAPI service ที่รับ log batch จาก GodEye ผ่าน HTTP POST webhook แล้ววิเคราะห์ด้วย Ollama (local LLM) เพื่อ return JSON summary ประกอบด้วย health score, anomaly list, และคำอธิบาย root cause ให้ dashboard แสดงผล

### ตำแหน่งใน Platform

```
GodEye (api-logs / aiops-ctl)
        │
        │  POST /analyze  (webhook push)
        ▼
  log-analyzer  :8200          ← service นี้
        │
        ├── POST /predict   →  aiops-ml  :8100  (anomaly detection)
        ├── POST /explain   →  aiops-ml  :8100  (LLM root cause)
        └── /api/generate   →  Ollama    :11434 (log pattern analysis)
        │
        ▼
  JSON summary response  →  aiops-fe dashboard
```

---

## 2. Project Structure

```
log-analyzer/
├── app/
│   ├── main.py               # FastAPI app + lifespan
│   ├── config.py             # config.yaml loader (pydantic-settings)
│   ├── models/
│   │   ├── request.py        # Pydantic input models
│   │   └── response.py       # Pydantic output models
│   ├── services/
│   │   ├── log_processor.py  # parse + group incoming logs
│   │   ├── aiops_ml.py       # client for aiops-ml /predict + /explain
│   │   └── ollama.py         # client สำหรับ Ollama /api/generate
│   └── routers/
│       ├── analyze.py        # POST /analyze  (main endpoint)
│       └── health.py         # GET /healthz
├── config.yaml.example
├── requirements.txt
├── Makefile
└── Dockerfile
```

---

## 3. Tech Stack

| Layer | Choice | เหตุผล |
|---|---|---|
| Language | Python 3.12 | เหมือน aiops-ml เดิม |
| Framework | FastAPI | เหมือน aiops-ml เดิม |
| HTTP client | `httpx` (async) | non-blocking, compatible กับ FastAPI |
| Config | `pydantic-settings` + `config.yaml` | ตาม pattern ของ platform (ADR-005) |
| LLM client | `httpx` → Ollama `/api/generate` | ตาม llm_handler.py ที่มีอยู่ใน docs |
| Validation | `pydantic` v2 | เหมือน aiops-ml เดิม |

---

## 4. Configuration (`config.yaml.example`)

```yaml
http:
  addr: "0.0.0.0:8200"

logger:
  level: "info"   # debug | info | warning | error

aiops_ml:
  base_url: "http://aiops-ml:8100"
  timeout: "60s"
  enabled: true    # false = skip ML, วิเคราะห์ด้วย Ollama อย่างเดียว

ollama:
  base_url: "http://localhost:11434"
  model: "qwen2.5:14b"       # ปรับตาม model ที่ deploy ไว้
  timeout: "120s"
  temperature: 0.1            # ต่ำ = consistent, factual

analysis:
  max_log_entries: 500        # cap per request ป้องกัน OOM
  severity_filter: "warn"     # รับเฉพาะ warn ขึ้นไป (info = เยอะเกิน)
  health_score:
    critical_weight: 2.0      # error/fatal นับหนักกว่า warn
    warn_weight: 1.0
    score_floor: 0.0
    score_ceiling: 100.0
```

**Load pattern** (เหมือน aiops-ml): อ่านจาก `config.yaml` ใน working directory, fallback ไป `config.yaml.example` ถ้าไม่เจอ

---

## 5. API Contract

### POST /analyze  ← Main endpoint

**Request body (JSON):**

```json
{
  "request_id": "req-abc123",
  "tenant_id": "internal",
  "window": {
    "from": "2026-05-21T10:00:00Z",
    "to":   "2026-05-21T10:05:00Z"
  },
  "entries": [
    {
      "time":     "2026-05-21T10:01:23Z",
      "host":     "pl-db-01",
      "service":  "postgres",
      "severity_text":   "error",
      "severity_number": 17,
      "msg":      "connection pool exhausted: max 100 reached",
      "service_profile": "db_postgres",
      "criticality":     "gold",
      "fields": {
        "trace_id": "abc123",
        "remote_ip": "10.0.0.5"
      }
    }
  ]
}
```

**Field definitions:**

| Field | Type | Required | Description |
|---|---|---|---|
| `request_id` | string | no | idempotency key, echo กลับใน response |
| `tenant_id` | string | yes | tenant scope (ตาม ADR-012) |
| `window.from` | RFC3339 | yes | ช่วงเวลาเริ่มต้นของ log batch |
| `window.to` | RFC3339 | yes | ช่วงเวลาสิ้นสุดของ log batch |
| `entries` | array | yes | log entries (canonical schema per ADR-017) |
| `entries[].time` | RFC3339 | yes | timestamp ของ log |
| `entries[].host` | string | yes | hostname ต้องตรงกับ api-inventory |
| `entries[].service` | string | yes | ชื่อ service/app |
| `entries[].severity_text` | string | yes | `trace`/`debug`/`info`/`warn`/`error`/`fatal` |
| `entries[].severity_number` | int | yes | OTEL 1-24 |
| `entries[].msg` | string | yes | log body (`_msg` จาก VictoriaLogs) |
| `entries[].service_profile` | string | no | `db_postgres`, `app_jvm`, ฯลฯ |
| `entries[].criticality` | string | no | `gold`/`silver`/`bronze` |
| `entries[].fields` | object | no | extra attributes passthrough |

**Response `200` (JSON):**

```json
{
  "request_id": "req-abc123",
  "tenant_id": "internal",
  "window": {
    "from": "2026-05-21T10:00:00Z",
    "to":   "2026-05-21T10:05:00Z"
  },
  "analyzed_at": "2026-05-21T10:05:03Z",
  "health_score": 42.5,
  "status": "critical",
  "hosts": [
    {
      "host":             "pl-db-01",
      "service_profile":  "db_postgres",
      "criticality":      "gold",
      "entry_count":      47,
      "error_count":      12,
      "warn_count":       8,
      "health_score":     35.0,
      "status":           "critical",
      "anomalies": [
        {
          "metric":   "pg_stat_activity_count",
          "score":    0.87,
          "severity": "critical",
          "current_value":   450.0,
          "baseline_mean":   45.0,
          "predicted_breach_at": "2026-05-21T10:15:00Z"
        }
      ],
      "top_errors": [
        {
          "msg":     "connection pool exhausted: max 100 reached",
          "count":   9,
          "first_seen": "2026-05-21T10:01:23Z",
          "last_seen":  "2026-05-21T10:04:55Z"
        }
      ],
      "explanation": {
        "summary":        "Connection pool exhaustion on pl-db-01 caused by spike in concurrent queries...",
        "likely_causes":  ["pg_stat_activity_count exceeded 400 (10x baseline)", "Lock contention from batch job"],
        "affected_metrics": ["pg_stat_activity_count", "pg_locks_count"],
        "suggested_actions": ["Increase max_connections", "Investigate long-running queries via pg_stat_activity"]
      }
    }
  ],
  "summary": "1 critical host, 0 warning hosts out of 1 analyzed. Primary issue: connection pool exhaustion on pl-db-01 (gold tier).",
  "sources": {
    "aiops_ml_used":  true,
    "ollama_used":    true,
    "ollama_model":   "qwen2.5:14b"
  }
}
```

**Field definitions (response):**

| Field | Description |
|---|---|
| `health_score` | Overall score 0–100 (100 = healthy, 0 = all down). Weighted average ของทุก host โดย criticality |
| `status` | `"ok"` / `"warning"` / `"critical"` — derived จาก `health_score` |
| `hosts[].health_score` | Per-host score คำนวณจาก error/warn ratio + anomaly scores |
| `hosts[].anomalies` | มาจาก aiops-ml `/predict` (ถ้า `aiops_ml.enabled: true`) |
| `hosts[].top_errors` | top 5 error messages ที่เกิดบ่อยที่สุด (group by msg) |
| `hosts[].explanation` | มาจาก aiops-ml `/explain` + Ollama วิเคราะห์ log patterns |
| `sources` | บอก dashboard ว่าใช้ service ไหนในการวิเคราะห์ |

**Health score thresholds:**

| Score | Status |
|---|---|
| ≥ 70 | `ok` |
| 40–69 | `warning` |
| < 40 | `critical` |

**Error responses:**

```json
// 400 - Bad request
{"error": "window.from must be before window.to"}

// 422 - Validation error (FastAPI standard)
{"detail": [...]}

// 502 - Upstream unreachable
{"error": "aiops-ml unreachable: connection refused"}

// 503 - Ollama unreachable
{"error": "ollama unreachable: connection refused"}
```

---

### GET /healthz

```json
{
  "status": "ok",
  "ollama": "reachable",
  "ollama_model": "qwen2.5:14b",
  "aiops_ml": "reachable"
}
```

---

## 6. Processing Logic (step-by-step)

### Step 1 — Validate + Filter

```python
# รับ entries ทั้งหมด
# filter เฉพาะ severity_number >= threshold ตาม config
# cap ที่ max_log_entries (ตัดจากท้าย = เก็บล่าสุด)
# validate window.from < window.to
```

### Step 2 — Group by Host

```python
# group entries → Dict[hostname, List[LogEntry]]
# extract unique hosts list
# per host: นับ error_count (severity_number >= 17), warn_count (13-16)
# per host: top 5 error messages โดย count (group by msg text)
```

### Step 3 — Call aiops-ml /predict (async, per host)

```python
# ถ้า aiops_ml.enabled = true:
#   สำหรับแต่ละ host ที่มี service_profile รู้จัก (linux/postgresql/mongodb/windows):
#     POST aiops-ml/predict
#     body: {"hosts": {"names": [hostname]}, "window": "2h", "horizon": "30m"}
#   รัน concurrent ด้วย asyncio.gather()
#   timeout ตาม config
#   ถ้า 502/timeout: log warning แล้วข้ามไป (ไม่ fail ทั้ง request)
```

### Step 4 — Call Ollama (log pattern analysis)

```python
# สำหรับแต่ละ host ที่มี error/warn logs:
#   สร้าง prompt จาก:
#     - host info (hostname, service_profile, criticality)
#     - top error messages + counts
#     - anomaly scores จาก step 3 (ถ้ามี)
#     - window timeframe
#   POST ollama /api/generate
#   parse JSON จาก response (prompt ให้ตอบ JSON)
#   ถ้า parse fail: ใช้ raw text เป็น summary
```

**Ollama Prompt Template:**

```
You are an IT operations analyst. Analyze the following log data and return a JSON object only, no markdown.

Host: {hostname}
Profile: {service_profile}
Criticality: {criticality}
Time window: {from} to {to}

Top errors (message: count):
{top_errors_formatted}

Anomaly scores (from ML model):
{anomalies_formatted}

Return JSON with exactly these keys:
{
  "summary": "one sentence describing the main issue",
  "likely_causes": ["cause 1", "cause 2"],
  "affected_metrics": ["metric1", "metric2"],
  "suggested_actions": ["action 1", "action 2"]
}
```

### Step 5 — Call aiops-ml /explain (optional, gold tier only)

```python
# ถ้า aiops_ml.enabled = true AND host.criticality == "gold" AND มี anomalies:
#   POST aiops-ml/explain
#   body: {"host": hostname, "window": {...}, "anomalies": [...from step 3]}
#   ใช้ explanation จาก aiops-ml แทน Ollama สำหรับ gold tier
#   ถ้า 503 (LLM unreachable): fallback ไปใช้ Ollama explanation จาก step 4
```

### Step 6 — Calculate Health Scores

```python
# Per-host health score:
#   base = 100
#   deduction = (error_count * critical_weight + warn_count * warn_weight) / entry_count * 100
#   anomaly_penalty = max(anomaly.score for anomaly in anomalies) * 30  # max 30 points
#   host_score = max(0, base - deduction - anomaly_penalty)

# Overall health score:
#   criticality_multiplier = {"gold": 3, "silver": 2, "bronze": 1}
#   weighted_sum = Σ (host_score * criticality_multiplier[criticality])
#   weight_total = Σ criticality_multiplier[criticality]
#   overall_score = weighted_sum / weight_total
```

### Step 7 — Assemble Response

```python
# สร้าง AnalysisResponse ตาม schema ใน section 5
# status = "critical" if score < 40, "warning" if < 70, else "ok"
# summary = คำอธิบายรวม (สร้างจาก code ไม่ต้องถาม LLM อีกรอบ)
#   format: "{n} critical, {n} warning, {n} ok hosts out of {total}. Primary issue: ..."
```

---

## 7. Ollama Client (`app/services/ollama.py`)

Pattern จาก `llm_handler.py` ใน docs:

```python
import httpx
import json

async def generate(
    prompt: str,
    model: str,
    base_url: str,
    timeout: float = 120.0,
    temperature: float = 0.1,
) -> str:
    """
    Call Ollama /api/generate
    Returns: raw text response
    Raises: OllamaError on failure
    """
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature},
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(f"{base_url}/api/generate", json=payload)
        resp.raise_for_status()
        body = resp.json()
        return body.get("response", "")


def parse_json_response(text: str) -> dict:
    """
    Parse JSON from LLM response
    Strip markdown code fences if present (```json ... ```)
    Fallback: return {"summary": text, "likely_causes": [], ...}
    """
    clean = text.strip()
    if clean.startswith("```"):
        lines = clean.split("\n")
        clean = "\n".join(lines[1:-1])  # strip first and last line
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        return {
            "summary": text[:500],  # truncate ถ้า LLM ตอบ plain text
            "likely_causes": [],
            "affected_metrics": [],
            "suggested_actions": [],
        }
```

---

## 8. aiops-ml Client (`app/services/aiops_ml.py`)

```python
import httpx
from app.models.response import AnomalyScore, Explanation

async def predict(
    hostnames: list[str],
    window: str,
    horizon: str,
    base_url: str,
    timeout: float = 60.0,
) -> dict:
    """
    POST {base_url}/predict
    Returns raw response dict or None on failure
    """
    ...

async def explain(
    host: str,
    window_from: str,
    window_to: str,
    anomalies: list[dict],
    base_url: str,
    timeout: float = 60.0,
) -> dict | None:
    """
    POST {base_url}/explain
    Returns raw response dict or None on failure
    """
    ...
```

---

## 9. Pydantic Models

### `app/models/request.py`

```python
from pydantic import BaseModel, Field
from datetime import datetime
from typing import Any

class TimeWindow(BaseModel):
    from_: datetime = Field(alias="from")
    to: datetime

    model_config = {"populate_by_name": True}

class LogEntry(BaseModel):
    time: datetime
    host: str
    service: str
    severity_text: str
    severity_number: int
    msg: str
    service_profile: str | None = None
    criticality: str | None = None
    fields: dict[str, Any] = {}

class AnalyzeRequest(BaseModel):
    request_id: str | None = None
    tenant_id: str
    window: TimeWindow
    entries: list[LogEntry] = Field(min_length=1)
```

### `app/models/response.py`

```python
from pydantic import BaseModel
from datetime import datetime

class AnomalyScore(BaseModel):
    metric: str
    score: float
    severity: str
    current_value: float | None = None
    baseline_mean: float | None = None
    predicted_breach_at: datetime | None = None

class TopError(BaseModel):
    msg: str
    count: int
    first_seen: datetime
    last_seen: datetime

class Explanation(BaseModel):
    summary: str
    likely_causes: list[str] = []
    affected_metrics: list[str] = []
    suggested_actions: list[str] = []

class HostAnalysis(BaseModel):
    host: str
    service_profile: str | None = None
    criticality: str | None = None
    entry_count: int
    error_count: int
    warn_count: int
    health_score: float
    status: str
    anomalies: list[AnomalyScore] = []
    top_errors: list[TopError] = []
    explanation: Explanation | None = None

class Sources(BaseModel):
    aiops_ml_used: bool
    ollama_used: bool
    ollama_model: str

class AnalyzeResponse(BaseModel):
    request_id: str | None = None
    tenant_id: str
    window: dict
    analyzed_at: datetime
    health_score: float
    status: str
    hosts: list[HostAnalysis]
    summary: str
    sources: Sources
```

---

## 10. `requirements.txt`

```
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
httpx>=0.27.0
pydantic>=2.7.0
pydantic-settings>=2.3.0
pyyaml>=6.0
```

---

## 11. `Makefile`

```makefile
run:
	uvicorn app.main:app --host 0.0.0.0 --port 8200 --reload

test:
	pytest tests/ -v

lint:
	ruff check app/

format:
	ruff format app/

build:
	docker build -t log-analyzer .
```

---

## 12. `Dockerfile`

```dockerfile
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY config.yaml.example ./config.yaml.example

EXPOSE 8200

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8200"]
```

---

## 13. Important Platform Conventions

สิ่งที่ต้อง follow เพื่อให้ integrate กับ GodEye ได้:

1. **Timestamps** — ใช้ RFC3339 ใน response (`2026-05-21T10:00:00Z`) ตาม ADR-014
2. **Error format** — `{"error": "message"}` เสมอ ไม่ใช่ `{"message": ...}` หรือรูปแบบอื่น
3. **Health endpoint** — `/healthz` (ไม่ใช่ `/health`) ตาม platform pattern
4. **Config** — อ่านจาก `config.yaml` ใน working dir, อย่า hardcode ค่าใดๆ
5. **Host identity** — field ชื่อ `host` (ไม่ใช่ `hostname`) ตาม ADR-017 canonical log schema
6. **Severity numbers** — OTEL standard: warn = 13-16, error = 17-20, fatal = 21-24
7. **Concurrent upstream calls** — ใช้ `asyncio.gather()` สำหรับ per-host calls อย่า loop sequential
8. **Graceful degradation** — ถ้า aiops-ml หรือ Ollama ไม่ตอบ ให้ลด feature ลงแต่ไม่ return error ทั้ง request

---

## 14. ลำดับการ implement (แนะนำ)

1. **Project scaffold** — สร้าง structure, `main.py`, `config.py`, `requirements.txt`
2. **Models** — `request.py` + `response.py` (Pydantic)
3. **Health endpoint** — `/healthz` + Ollama reachability check
4. **Log processor** — `log_processor.py`: filter, group, top_errors, health score calculation
5. **Ollama client** — `ollama.py`: generate + parse_json_response
6. **Main endpoint** — `/analyze` รับ request + process + Ollama วิเคราะห์ (ยังไม่ต้อง aiops-ml)
7. **aiops-ml client** — `aiops_ml.py`: predict + explain
8. **Wire aiops-ml** — integrate predict/explain เข้าใน analyze flow
9. **Config** — `config.yaml.example` + `Makefile` + `Dockerfile`
10. **Tests** — test health score calculation, test parse_json_response, test request validation
