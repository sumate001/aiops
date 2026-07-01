# GodEye ↔ aiops Integration Guide

ระบบ aiops คุยกับ GodEye ได้ 2 โหมด — เลือกที่ `godeye.enabled` ใน config.yaml:

### โหมด SYNC (default, แนะนำ — `godeye.enabled: false`)
HTTP web service ปกติ: ยิง request → ได้ JSON กลับมาในคำตอบเลย ไม่ต้องทำ callback endpoint
```
GodEye  ──POST /ingest (log batch)──▶  aiops วิเคราะห์
GodEye  ◀────── AnalyzeResponse JSON ──┘  (รอจนเสร็จแล้วคืนในคำตอบ)
```

### โหมด ASYNC (`godeye.enabled: true` + ตั้ง callback_url)
คืน 202 ทันที แล้วยิงผลไป callback ทีหลัง (เหมาะกับ batch ใหญ่/วิเคราะห์นาน)
```
GodEye  ──POST /ingest──▶ aiops (202 ทันที) ──วิเคราะห์ background──▶ POST callback กลับ GodEye
```

อ่านผลย้อนหลังได้เสมอที่: `GET /api/results` (เก็บ 500 รายการล่าสุด)

> ปัจจุบันตั้งเป็น **SYNC** ไว้ตามที่ GodEye ต้องการ ("ส่ง request → return JSON")

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

### การตอบกลับของ /ingest (ขึ้นกับ godeye.enabled)
- **`godeye.enabled: false`** (SYNC, default) → คืน `200` พร้อม `AnalyzeResponse` JSON ในคำตอบเลย
  GodEye อ่าน JSON ได้ทันที **ไม่ต้องมี callback endpoint** ← แบบที่ GodEye ขอ
- **`godeye.enabled: true`** + มี `callback_url` → คืน `202 Accepted` ทันที แล้ววิเคราะห์ background ส่งผลทาง callback
  ```json
  { "status": "accepted", "request_id": "req-123", "entries": 2,
    "callback_url": "http://godeye:9000/api/aiops/callback" }
  ```

> ⏱️ SYNC = GodEye ต้องรอจน pipeline เสร็จก่อนได้ JSON: 1 host ≈ ไม่กี่วินาที (A2 speed mode),
> หลาย host → A2 ทำทีละตัวอาจนานขึ้น → **ตั้ง HTTP client timeout ฝั่ง GodEye ≥ 60–120s**

---

## ขา 1b — GodEye ส่ง metrics เข้า aiops (ผ่าน `/ingest` ช่องเดียวกัน)

metric ส่งมาใน `entries[]` ชุดเดียวกับ log ได้เลย — แยกด้วยฟิลด์ **`type: "metric"`**
(log ใช้ `type: "log"` หรือไม่ใส่ก็ได้) จะส่งปนกันใน batch เดียว หรือแยก batch ก็ได้

### รูปแบบ metric entry
```json
{
  "type": "metric",
  "_time": "2026-06-28T10:00:00Z",
  "host": "pos-db-01",
  "metric": "cpu_usage",
  "value": 96.4,
  "unit": "percent",
  "service": "mysql",
  "criticality": "gold"
}
```

| ส่งมาเป็น | aiops ใช้ | หมายเหตุ |
|---|---|---|
| `_time` / `time` / `timestamp` | timestamp | ถ้าไม่มี → skip |
| `metric` / `name` / `metric_name` | ชื่อ metric | ถ้าไม่มี → skip |
| `value` / `_value` / `metric_value` | ค่า (number) | แปลงไม่ได้ → skip |
| `host` (fallback `hostname`) | host | host เดียวกับ log จะถูกรวมเข้า host เดียวกัน |
| `unit` | unit | optional |
| `service_profile`, `criticality` | health weighting | optional |
| ฟิลด์อื่นๆ | flatten เข้า `labels` | |

### aiops ทำอะไรกับ metric
1. group ตาม host + ชื่อ metric แล้วดูค่า **ล่าสุด** เทียบ threshold ใน `config.yaml → analysis.metric_thresholds`
2. ถ้าเกิน → สร้าง **anomaly** (`metric`, `score`, `severity`, `current_value`, `baseline_mean`, `predicted_breach_at`)
   - `severity`: `high` = เกิน critical, `medium` = เกิน warn
   - `baseline_mean` = ค่าเฉลี่ยของ sample ใน window
   - `predicted_breach_at` = คาดการณ์เวลาที่จะชน critical (linear fit, ต้องมี ≥3 sample และกำลังพุ่งเข้าหา threshold)
3. anomaly นี้ feed เข้า health score / synthesizer / prediction / A2 **เหมือน anomaly ของ log** ทุกอย่าง
4. host ที่มี **metric อย่างเดียว (ไม่มี log)** ก็วิเคราะห์ได้ — health คิดจาก anomaly ล้วน

> ⚠️ metric ที่ **ไม่ match key ใน `metric_thresholds`** จะถูกข้าม (ไม่มี baseline ให้ตัดสิน)
> key เป็น substring ของชื่อ metric (เช่น key `cpu` match `cpu_usage`, `node_cpu_util`)
> default ในโค้ด: cpu / memory / disk_usage / disk_free / load / latency / error_rate / temperature

### ทดสอบ metric (curl)
```bash
curl -s -X POST http://localhost:8200/ingest -H "Content-Type: application/json" -d '{
  "tenant_id":"test",
  "entries":[
    {"type":"metric","_time":"2026-06-28T10:00:00Z","host":"pos-db-01","metric":"cpu_usage","value":82},
    {"type":"metric","_time":"2026-06-28T10:01:00Z","host":"pos-db-01","metric":"cpu_usage","value":97},
    {"type":"log","_time":"2026-06-28T10:01:00Z","host":"pos-db-01","service":"mysql",
     "severity_text":"error","message":"Lock wait timeout exceeded"}
  ]
}' | python3 -m json.tool
```

### ⚠️ Troubleshooting: "ส่ง log+metric แต่ aiops วิเคราะห์แต่ log"

ยืนยันด้วย curl ข้างบนแล้วว่า pipeline ฝั่ง aiops วิเคราะห์ metric ได้ปกติเมื่อ payload ถูกต้อง — ถ้า GodEye
ส่งมาแล้วเห็นแต่ log ในผลลัพธ์ ให้เช็ค `Ingesting N log entries + M metrics (from T raw)` ใน server log ก่อน:

- **`M metrics = 0` และ `T raw` เท่ากับ log count พอดี** → GodEye **ไม่ได้ส่ง metric entry มาเลย**
  ไม่ใช่ aiops ทิ้ง ที่พบบ่อยสุดคือ GodEye ส่ง metric entry แต่ **ไม่มีฟิลด์ `"type": "metric"`**
  (adapter จะตีความเป็น log แล้ว skip ทิ้งเงียบๆ เพราะไม่มี `severity_text`/`message`)
- **`T raw` มากกว่า `log + metrics`** → มี entry ที่ parse ไม่ผ่านทั้งสองแบบ (ดู log บรรทัด
  `Skipped N entries with missing timestamp/value` และเช็คว่า field `_time`/`metric`/`value` ตรงชื่อไหม)
- metric ที่ parse ผ่านแต่ **ไม่ match key ใดใน `metric_thresholds`** จะไม่ปรากฏเป็น anomaly เลย
  (เงียบเหมือนกัน) — เช็คว่าใช้ default key (cpu/memory/disk_usage/…) หรือ config เอง

---

## ขา 2 — aiops ส่งผลกลับ GodEye: callback (เฉพาะโหมด ASYNC)

> ถ้าใช้ SYNC (default) **ข้ามส่วนนี้ได้เลย** — ผลมาในคำตอบของ /ingest อยู่แล้ว
> ส่วนนี้สำหรับกรณีอยากใช้ async (batch ใหญ่/วิเคราะห์นาน) เท่านั้น

### ตั้งค่า (config.yaml)
```yaml
godeye:
  callback_url: http://godeye:9000/api/aiops/callback   # ปลายทางรับผล
  callback_timeout: "10s"
  enabled: true                                          # true = เปิด async callback
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

### แบบ SYNC (default — GodEye อยากได้แบบนี้)
- [ ] aiops: `godeye.enabled: false` (ตั้งไว้แล้ว)
- [ ] GodEye: `POST /ingest` พร้อม `severity_text`/`severity_number` ที่ถูกต้อง → อ่าน JSON ที่ตอบกลับ
- [ ] GodEye: ตั้ง HTTP client timeout ≥ 60–120s (รอ pipeline เสร็จ)
- [ ] **ไม่ต้อง** ทำ callback endpoint

### แบบ ASYNC (ถ้าจะใช้ภายหลัง)
- [ ] aiops: `godeye.enabled: true` + `godeye.callback_url`
- [ ] GodEye: เปิด endpoint รับ callback (รับ JSON AnalyzeResponse, ตอบ 2xx)

### ทั้งสองแบบ
- [ ] (แนะนำ) เพิ่ม auth ระหว่าง GodEye↔aiops (shared header/token) — ยังไม่มีในปัจจุบัน

---

> ⚙️ สลับโหมด: แก้ `godeye.enabled` ใน config.yaml (หรือหน้า Settings) แล้ว **restart backend**
> (`bash deploy.sh --stop && bash deploy.sh --start`) — config อ่านตอน start เท่านั้น
> ⚠️ ถ้าสตาร์ทด้วย `sudo` process จะเป็น root — ต้อง `sudo` ตอน restart ด้วย
