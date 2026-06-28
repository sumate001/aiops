# GodEye ↔ aiops Integration Guide

ระบบ aiops เชื่อม GodEye แบบ 2 ทาง:

```
GodEye  ──(1) POST /ingest (log batch)──▶  aiops (วิเคราะห์ MoA pipeline)
                                              │
GodEye  ◀──(2) POST callback (ผลวิเคราะห์)──┘  (async, เมื่อวิเคราะห์เสร็จ)
```

อ่านผลได้ที่ dashboard ด้วย: `GET /api/results` (ดูย้อนหลัง 500 รายการ)

---

## ขา 1 — GodEye ส่ง log เข้า aiops: `POST /ingest`

ปลายทาง: `http://<aiops-host>:8200/ingest`

### รูปแบบ A) JSON (แนะนำ)
```json
{
  "request_id": "req-123",
  "tenant_id": "pos-store-01",
  "callback_url": "http://godeye:9000/api/aiops/callback",
  "window_from": null,
  "window_to": null,
  "entries": [
    {
      "_time": "2026-06-28T10:00:00Z",
      "host": "pos-db-01",
      "service": "mysql",
      "severity_text": "error",
      "severity_number": 17,
      "message": "Lock wait timeout exceeded; try restarting transaction"
    },
    {
      "_time": "2026-06-28T10:00:05Z",
      "host": "pos-db-01",
      "service": "mysql",
      "severity_text": "critical",
      "message": "Too many connections"
    }
  ]
}
```

ฟิลด์ระดับ request:
- `request_id`  — id ของ batch (optional, echo กลับใน callback)
- `tenant_id`   — fallback ถ้า entry ไม่มี tenant_id
- `callback_url`— ที่อยู่ปลายทางให้ส่งผลกลับ (override ค่าใน config.yaml ราย request)
- `window_from` / `window_to` — ถ้าไม่ใส่ จะ derive จาก min/max `_time` ของ entries
- `entries[]`   — log อย่างน้อย 1 รายการ (required)

### รูปแบบ B) NDJSON (stream)
Header: `Content-Type: application/x-ndjson`
Body: หนึ่ง JSON object ต่อบรรทัด (ไม่มี wrapper) — tenant_id = "internal", callback มาจาก config.yaml

### ฟิลด์ของแต่ละ entry (adapter แปลงให้อัตโนมัติ)
| ส่งมาเป็น | aiops ใช้ | หมายเหตุ |
|---|---|---|
| `_time` / `time` / `timestamp` / `EventReceivedTime` | timestamp | RFC3339; ถ้าไม่มี entry นั้นถูก skip |
| `host` (fallback `hostname`) | host | "?" / "unknown" → ใช้ hostname แทน |
| `service` | service | |
| `severity_text` | severity (syslog→OTEL: err→error, warning→warn, crit→fatal …) | |
| `severity_number` | severity number | string/int ก็ได้ |
| `message` (fallback `_msg`) | ข้อความ | `_msg` raw syslog จะถูกตัด header ให้ |
| `structured_data.*` | flatten เข้า fields | |
| `service_profile`, `criticality` | ใช้ใน ML predict + health weighting | optional |

### ⚠️ กับดักสำคัญ: severity threshold
entry ที่ `severity_number < 13` (ต่ำกว่า warn) **ถูกกรองทิ้ง** ก่อนวิเคราะห์
- ถ้า GodEye ไม่ส่ง `severity_text`/`severity_number` → default = info (9) → หายหมด → ผลว่าง
- **ต้องส่ง severity ของจริงมา** (error=17, warn=13, fatal=21)
- ปรับ threshold ได้ที่ config.yaml → `analysis.severity_filter` (warn | error | …)

### การตอบกลับของ /ingest
- **มี `callback_url` + `godeye.enabled: true`** → คืน `202 Accepted` ทันที, วิเคราะห์ background, ส่งผลทาง callback
  ```json
  { "status": "accepted", "request_id": "req-123", "entries": 2,
    "callback_url": "http://godeye:9000/api/aiops/callback" }
  ```
- **ไม่มี callback** → คืน `AnalyzeResponse` แบบ sync ในคำตอบเลย (caller รอจนวิเคราะห์เสร็จ)

---

## ขา 2 — aiops ส่งผลกลับ GodEye: callback

### ตั้งค่า (config.yaml)
```yaml
godeye:
  callback_url: http://godeye:9000/api/aiops/callback   # ปลายทางรับผล
  callback_timeout: "10s"
  enabled: true                                          # false = ไม่ส่ง webhook
```
(callback_url ใส่ใน config = default ทุก request, หรือ override ใน body /ingest ราย request)

GodEye ต้องเปิด endpoint รับ (เช่น `POST /api/aiops/callback`) ที่รับ JSON `AnalyzeResponse`
การส่งเป็น fire-and-forget: ถ้า callback ล่ม aiops จะ log warning เฉยๆ ไม่ retry, ไม่ crash

### Schema ของ callback payload (AnalyzeResponse)
```json
{
  "request_id": "req-123",
  "tenant_id": "pos-store-01",
  "window": { "from": "2026-06-28T10:00:00Z", "to": "2026-06-28T10:00:05Z" },
  "analyzed_at": "2026-06-28T10:00:08Z",
  "health_score": 0.0,
  "status": "critical",
  "summary": "1 critical, 0 warning, 0 ok hosts out of 1. Primary issue: ...",
  "hosts": [
    {
      "host": "pos-db-01",
      "service_profile": null,
      "criticality": null,
      "entry_count": 2,
      "error_count": 1,
      "warn_count": 0,
      "health_score": 0.0,
      "status": "critical",
      "anomalies": [
        { "metric": "if_score", "score": 0.82, "severity": "high",
          "current_value": null, "baseline_mean": null, "predicted_breach_at": null }
      ],
      "top_errors": [
        { "msg": "Lock wait timeout exceeded", "count": 1,
          "first_seen": "2026-06-28T10:00:00Z", "last_seen": "2026-06-28T10:00:00Z" }
      ],
      "explanation": { "summary": "...", "likely_causes": [], "affected_metrics": [], "suggested_actions": [] },
      "trend":      { "direction": "rising", "slope_per_hour": 0.0, "windows_analyzed": 1 },
      "prediction": { "risk_level": "high", "confidence": 0.8, "estimated_incident_in": null,
                      "contributing_signals": [], "recommendation": "", "matched_fingerprint": null },
      "mirofish": [
        { "frame": "Database", "lens": "...", "relevance": 0.8, "signal_hits": 5,
          "keyword_hits": 3, "top_keywords": ["lock","timeout","deadlock"], "insight": "..." }
      ],
      "synthesis": {
        "root_cause_chain": ["...", "..."],
        "confidence": 0.8,
        "fix_steps": ["...", "..."],
        "method": "llm",
        "top_frame": "Database", "top_frame_lens": "...", "anomaly_methods": []
      },
      "enrichment": {
        "query": "database lock wait timeout ...",
        "answer": "....(สรุปจาก web search)....",
        "sources": [ { "title": "...", "url": "https://..." } ]
      }
    }
  ],
  "sources": { "aiops_ml_used": false, "ollama_used": true, "ollama_model": "openai/gpt-oss-20b" }
}
```
ฟิลด์ที่ GodEye น่าจะใช้มากสุด: `health_score`, `status`, `summary`, และต่อ host →
`synthesis.root_cause_chain` + `synthesis.fix_steps` + `synthesis.confidence` (สาเหตุ + วิธีแก้),
`prediction.risk_level` (ทำนาย incident), `enrichment.answer/sources` (ความรู้จาก web)

---

## ทดสอบเร็ว (curl)

### sync (เห็นผลทันทีในคำตอบ — ไม่ใส่ callback)
```bash
curl -s -X POST http://localhost:8200/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "test",
    "entries": [
      {"_time":"2026-06-28T10:00:00Z","host":"pos-db-01","service":"mysql",
       "severity_text":"error","message":"Lock wait timeout exceeded"},
      {"_time":"2026-06-28T10:00:05Z","host":"pos-db-01","service":"mysql",
       "severity_text":"critical","message":"Too many connections"}
    ]
  }' | python3 -m json.tool
```

### async (จำลองการใช้จริงกับ GodEye — ใส่ callback)
```bash
curl -s -X POST http://localhost:8200/ingest \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "pos-store-01",
    "callback_url": "http://godeye:9000/api/aiops/callback",
    "entries": [
      {"_time":"2026-06-28T10:00:00Z","host":"pos-db-01","service":"mysql",
       "severity_text":"error","message":"Lock wait timeout exceeded"}
    ]
  }'
# → {"status":"accepted",...}  ผลจะถูก POST ไปที่ callback_url เมื่อวิเคราะห์เสร็จ
```

### ดูผลย้อนหลัง (แทน callback ระหว่างพัฒนา)
```bash
curl -s "http://localhost:8200/api/results?limit=5" | python3 -m json.tool
```

---

## Checklist ทำ integration จริง
- [ ] aiops: ตั้ง `godeye.callback_url` + `godeye.enabled: true` (config.yaml หรือหน้า Settings)
- [ ] GodEye: ยิง log batch มา `POST /ingest` พร้อม `severity_text`/`severity_number` ที่ถูกต้อง
- [ ] GodEye: เปิด endpoint รับ callback (รับ JSON AnalyzeResponse, ตอบ 2xx)
- [ ] ทดสอบ sync curl ก่อน (ดูว่า hosts ไม่ว่าง) → แล้วค่อยทดสอบ async + callback
- [ ] (แนะนำ) เพิ่ม auth ระหว่าง GodEye↔aiops (เช่น shared header/token) — ยังไม่มีใน webhook.py ปัจจุบัน
