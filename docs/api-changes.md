# API / schema changes

บันทึกเฉพาะสิ่งที่ consumer ภายนอก (GodEye, Grafana, n8n ฯลฯ) ต้องรู้
ไม่ใช่ changelog ของทุก commit

---

## 2026-08-06 — A4 memory + feedback

### เพิ่มใน response ของ `POST /analyze` (additive ทั้งหมด ไม่ breaking)

`HostAnalysis`:

```jsonc
{
  "detected_service": "mysql",      // mysql | postgresql | mongodb | null
  "memory_hits": [                   // เคสเก่า + playbook ที่ A4 ค้นเจอ
    {
      "point_id": "…",
      "kind": "analysis",            // analysis | playbook
      "similarity": 0.98,            // cosine ดิบ — ใช้ตัดสิน confidence
      "final_score": 1.05,           // ถ่วงน้ำหนักแล้ว ใช้เรียงลำดับเท่านั้น เกิน 1.0 ได้
      "symptom_text": "…",
      "root_cause_chain": ["…"],
      "fix_steps": ["…"],
      "verified": true,              // มีคนยืนยันแล้วหรือยัง
      "actual_fix": "…",             // สิ่งที่แก้ได้จริงตามที่คนใส่มา
      "occurrence_count": 3,
      "created_at": "…", "days_ago": 12,
      "title": null,                 // playbook เท่านั้น
      "verify_steps": [], "docs_url": null
    }
  ]
}
```

`Synthesis`:

```jsonc
{
  "memory_refs": ["point_id ที่ AA อ้างจริง"],
  "memory_influenced": true,
  "playbook_refs": [],
  "playbook_influenced": false
}
```

> ⚠️ **ถ้าจะเอา `similarity` ไปแสดงเป็น "ตรงกันกี่ %" ให้ใช้ `similarity` เท่านั้น**
> `final_score` ถ่วงน้ำหนักด้วย verified/recency/occurrence แล้วและเกิน 1.0 ได้
> เอาไปแสดงเป็นเปอร์เซ็นต์จะได้ตัวเลขที่ตีความไม่ได้

> **`similarity` ของโมเดลนี้อยู่ในช่วง 0.72–0.96 ไม่ใช่ 0–1** — `multilingual-e5-small`
> ไม่เคยให้คะแนนใกล้ศูนย์ ข้อความที่ไม่เกี่ยวกันเลยยังได้ ~0.72-0.79 ถ้าจะตั้ง
> threshold หรือทำ heatmap ที่ฝั่ง consumer ต้องคิดบนช่วงนี้

### Endpoint ใหม่

```
POST   /api/results/{result_id}/hosts/{host}/feedback
GET    /api/results/{result_id}/hosts/{host}/feedback
GET    /api/memory/stats
POST   /api/memory/{point_id}/deprecate?tenant_id=…
DELETE /api/memory/{point_id}?confirm=true
```

รหัสตอบกลับที่ไม่ใช่ 200 และความหมาย:

| code | เมื่อไหร่ |
|---|---|
| 400 | verdict ไม่ถูกต้อง · `wrong` ที่ไม่ส่ง `actual_root_cause`/`actual_fix` · DELETE ที่ไม่ส่ง `confirm=true` |
| 404 | ไม่มี memory point สำหรับ (result_id, host) นั้น |
| **409** | ยิง feedback หรือ DELETE ใส่ **playbook** — ให้แก้ไฟล์แล้ว re-seed แทน |
| 503 | `memory.enabled: false` หรือ Qdrant ติดต่อไม่ได้ |

### Metric ใหม่

```
godeyes_memory_hits_total
godeyes_memory_verified_hits_total
godeyes_playbook_hits_total{engine}
godeyes_memory_search_duration_seconds
godeyes_synthesis_memory_influenced_total
godeyes_feedback_total{verdict}
```

### Config ใหม่

`config.yaml` ต้องมี block `memory:` และ `service_detection:` — ถ้าไม่มี จะใช้ค่า
default ในโค้ด (memory.enabled = true) ดู `config.yaml.example`
**ตั้ง `memory.enabled: false` เพื่อปิดทั้งหมด** — pipeline ทำงานครบเหมือนเดิม

---

## ก่อนหน้านี้ — `PredictionInfo.confidence` → `self_confidence`

breaking change ของ response schema · เปลี่ยนชื่อเพื่อไม่ให้สับสนกับ
`Synthesis.confidence` ซึ่งเป็นคนละอย่างกัน:

- `Synthesis.confidence` — ความมั่นใจรวมของ AA ต่อ root cause (เจ้าของค่าสุดท้าย)
- `PredictionInfo.self_confidence` — ความมั่นใจของ predictor ต่อ risk estimate ของตัวเอง

consumer ที่อ่าน `prediction.confidence` ต้องเปลี่ยนไปอ่าน `prediction.self_confidence`
