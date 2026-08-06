# HANDOFF — งานที่ค้าง (2026-08-06)

สรุปสถานะเพื่อไปทำต่อบนเครื่องอื่น

---

## 🆕 รอบ 2026-08-06 — A4 Memory + Feedback loop (branch `feat/a2-grounding-and-playbooks`)

**branch นี้ยังไม่ merge เข้า main**

ทำครบ Phase 1, 2, 2.11, 3 ของแผน godeyes — ระบบจำเคสที่เคยวิเคราะห์ได้ เอากลับมาใช้
และให้คนยืนยัน/แก้ได้ · 216 tests ผ่าน · verify กับ Qdrant + `/analyze` จริงทุกขั้น

### สิ่งที่เพิ่มเข้ามา

| | |
|---|---|
| **A4 memory** | Qdrant 1.19 (`aiops-qdrant`, volume `aiops-qdrant-data`) · dense `multilingual-e5-small` + sparse BM25 fused ด้วย RRF · เข้า pipeline เป็น Phase 2.5 |
| **Playbook** | 31 entries (mysql/postgresql/mongodb) แก้ปัญหา cold start · `scripts/seed_playbooks.py` (idempotent) |
| **Feedback** | `POST /api/results/{id}/hosts/{host}/feedback` + `/api/memory/*` · UI มีปุ่มแยกตาม host |
| **service_detector** | regex → mysql/postgresql/mongodb สำหรับ filter ของ A4 |
| **normalize.py** | ยุบ log ให้เทียบกันได้ แต่กัน `ORA-01555` / `errno 111` / `HTTP 503` ไว้ |
| **A2 fix (Phase 1)** | gate ไม่ให้คำตอบไร้แหล่งอ้างอิงเข้า judge + รื้อ `build_query` |

### ค่าที่ "ไม่ตรงกับแผน" และเหตุผล — อย่าแก้กลับโดยไม่อ่านตรงนี้

- **`min_score` 0.45 → 0.80** · e5 ไม่เคยให้คะแนนใกล้ศูนย์ วัดจริง: เหมือนกันเป๊ะ 0.96,
  เรื่องเดียวกันคนละคำ 0.85, คนละเรื่องเลย 0.79, ภาษาไทยเรื่องอากาศ 0.72
  ค่า 0.45 จะรับทุก point ในคอลเลกชันผ่านหมด
- **confidence tier 0.80/0.65 → 0.90/0.84** เหตุผลเดียวกัน (0.65 จะติดทุกอย่าง)
- **`final_score` คำนวณจาก fused RRF score ไม่ใช่ similarity** ตามสูตรในแผน
  ถ้าใช้ similarity ผลของ BM25 หายหมด — วัดแล้ว top-1 ตกจาก 8/8 เหลือ 6/8
- **A4 อยู่หลัง A3 ไม่ใช่ขนานกัน** เพราะ symptom_text สร้างจาก frame ของ A3
- **เรียกว่า playbook ไม่ใช่ knowledge_doc** — ชื่อ knowledge ถูกใช้ไปแล้ว 2 ที่
- **Qdrant อยู่ใน `deploy.sh` ไม่ใช่ docker-compose** — compose อยู่บน orphan branch

### ✅ ปิดไปแล้วในรอบเดียวกัน

- **merge เข้า main** แล้ว (`aa55fc2`)
- **Phase 4 A1b Chronos** เสร็จ (`6ee3dac`) — แต่ `min_history_hours` ต้องเป็น **168** ไม่ใช่ 72
  ตามแผน เพราะ 3 วันวัดแล้วยังสร้าง false positive
- **Grafana** เพิ่ม 8 panel บน branch `godeyes` (`6ab2028`, `48a60e8`)
- **SearXNG drift** แก้ที่ต้นเหตุแล้ว — config เข้า repo, `deploy.sh` mount read-only
  ทั้งสอง branch sync กันแล้ว

### ที่ยังค้าง

1. **A2 ยังอ่อน** — SearXNG เหลือ engine ที่ตอบจริงไม่กี่ตัว ดู `docs/a2-status.md`
   ทางเลือกที่ยังเปิดอยู่คือย้ายไป Perplexity Search API
2. `_clean_error` ของ A2 ยังกลืน error code — ควรเปลี่ยนไปใช้ `normalize.py` ที่มีแล้ว
3. หน้า pipeline ดูได้แค่ผลล่าสุด ถ้ารับ `?id=` จะ debug ง่ายขึ้นมาก
4. **A1b: `health_score` จับ seasonality ได้ไม่คมเท่า `error_count`** — dip 92→40
   เป็นสัดส่วนเบากว่า spike 3→40 มาก โมเดลเลย smooth ทิ้ง ยังเห็น breach ในเคสที่
   เป็น pattern ปกติ ให้ดู `breach_magnitude` ประกอบ ไม่ใช่ดูแค่ boolean
5. **A1b ยังไม่เคยรันกับข้อมูลจริงที่มีประวัติ 168 ชม.** — verify ด้วย DB แยก
   (`/tmp/a1b-test`) ที่ seed ประวัติสังเคราะห์ 8 วัน เพราะ `log_analyzer.db` เป็นของ root
6. **UI ยังไม่แสดง forecast band** — `HostAnalysis.forecast` มีใน response แล้ว
   แต่หน้า results/pipeline ยังไม่ render

### เอกสารที่เกี่ยวข้อง

- `docs/a2-status.md` — ผลตรวจ A2 + สิ่งที่แก้ไป
- `docs/playbooks.md` — playbook + service detection
- `DEVELOPER.md` §10.5 — วิธีเพิ่ม playbook / reset collection / กับดักที่ต้องรู้

---

## 🆕 รอบ 2026-07-02 — Root-cause depth + false-critical fixes (branch `feat/godeye-metrics-ingestion`)

โจทย์: ผลวิเคราะห์ที่ส่งให้ GodEye "สั้นและตื้น" + ทุก host ออก critical หมด — ไล่จนเจอ 3 กลุ่มปัญหา:

**1. LLM judge ไม่เคยเห็นหลักฐานจริง** (commit `f4fca97`)
- แยก AA เป็น 2 pass: rule pass (phase 3, สร้าง A2 query) → LLM judge (phase 5, รันหลัง A2)
- judge เห็น: ข้อความ error/warn จริง, MiroFish per-frame insight, ค่า metric (current/baseline),
  rule chain, ผล A2 web research — เดิมเห็นแค่ชื่อ frame + ตัวเลข relevance
- prompt เขียนใหม่เน้น cause-vs-symptom; เพิ่ม field `synthesis.reasoning`; เลิก truncate 500 chars

**2. False criticals** (commit `7d8e158`)
- metric threshold percent เคยถูกใช้กับ metric หน่วย bytes/counter (เช่น `system_memory_usage_bytes`
  ถูกตีว่า "เกิน 95%") → เพิ่ม unit-mismatch guard ใน `metric_analyzer`
- health score: GodEye ส่งเฉพาะ warn+ → ratio เดิมอิ่มตัวที่ 100% เสมอ → ใช้ยอด pre-filter
  เป็นตัวหาร + cap deduction (warn -35, error -70, anomaly -30)
- `compute_top_errors` fallback เป็นข้อความ warn เมื่อไม่มี error (ให้ judge มีหลักฐานเสมอ)

**3. SearXNG โดน upstream แบน + summary อ่านไม่ได้** (commit `be465a1` + config นอก repo)
- A2: cache คำตอบต่อ query 6h + cooldown 60s ระหว่าง search จริง; query ไม่ fallback เป็น
  hostname/ชื่อ detector อีก (ไม่มีหลักฐาน → ข้าม A2)
- SearXNG container: ปิด duckduckgo/brave/startpage (CAPTCHA/429), ใช้ mojeek/qwant —
  **config อยู่ใน volume `/etc/searxng/settings.yml` ไม่ใช่ใน repo** (แก้ด้วย `docker exec -i`)
- `summary` เป็น plain text บรรทัดเดียวคั่น ` | ` + ล้าง markdown (UI GodEye ยุบ newline);
  รายละเอียดเต็มอยู่ใน `synthesis.*` / `enrichment.answer` (ฝั่ง GodEye จะไป render field พวกนี้เอง)

verify กับ traffic จริงจาก GodEye: host warn-only ได้ warning/65 (เดิม critical/0) และ
root_cause_chain ชี้ตัวจริง (beyla + bpf_probe_write_user + journal corruption) แทนประโยค generic

---

## 🆕 รอบ 2026-06-30 — Metrics ingestion + A2/SearXNG fix (branch `feat/godeye-metrics-ingestion`)

**1. GodEye metrics ingestion (ใหม่)** — เดิม entry `type="metric"` ถูกทิ้งทั้งหมด
- รับผ่าน `/ingest` ช่องเดียวกับ log → แปลงเป็น `AnomalyScore` (current_value/baseline_mean/predicted_breach_at)
- threshold ที่ `analysis.metric_thresholds` (cpu/memory/disk/load/latency/error_rate/temperature)
- ไฟล์: `app/services/metric_analyzer.py` (ใหม่), `godeyes_adapter.transform_metric()`, `app/models/request.py` (`MetricSample`), `analyze.py` (union log+metric hosts), `app/config.py` (`MetricThreshold`)
- **status escalation** (best practice): metric `high`→critical, `medium`→warning floor ไม่ให้คะแนนเฉลี่ยกลบ breach (`log_processor.escalate_status/worse_status`)
- ดู `docs/godeye-integration.md` (ขา 1b)

**2. A2 ค้าง/ช้า — เจอ root cause จริง: SearXNG `format=json` ปิด → 403 Forbidden**
- Perplexica research โยน unhandledRejection → `/api/search` ค้างจน 480s timeout → ได้ 0 sources
- แก้: `deploy.sh` `ensure_searxng_json()` (idempotent, รันทั้งตอน create + start container เดิม) — guard เดิมบั๊กไม่เคยเปิด json
- หลังแก้: A2 ~8-12s/host ได้ web sources จริง → ลด `perplexica.timeout` 480s→90s

**3. A2 warm-up ตอน boot** — `perplexica_client.warm_up()` ยิง search เปล่า 1 ครั้งใน lifespan (background, ไม่ block) โหลด embedding + prime provider cache → request แรกไม่ได้ 0 sources จาก cold start

**4. cleanup** — ลบ `perplexica.chat_model` ที่ตายแล้ว (A2 chat มาจาก `llm.perplexica` stage), unify embedding default → `Xenova/all-MiniLM-L6-v2`, แก้ score normalization ของ threshold ทิศ `below` (disk_free ฯลฯ)

> verify เต็ม pipeline จริง: 3-host (log+metric) เสร็จ ~84s, ทุก host ได้ A2 sources, metrics→anomaly→health→synthesis ครบ

---

## สถานะ pipeline ล่าสุด

| Stage | สถานะ | หมายเหตุ |
|-------|--------|----------|
| A1 Rule | ✅ ทำงาน | |
| A1 IF (log-ml) | ✅ ทำงาน | รันที่ port 3050 |
| A2 Perplexica | ✅ ทำงาน | Groq `openai/gpt-oss-20b`, speed mode — verify จริง ~24s (15 sources found, 5 saved) |
| A3 MiroFish | ✅ ทำงาน | Redis scenario → Software=100%, Hardware=20% |
| AA Synthesizer | ✅ ทำงาน | |

## ✅ เสร็จแล้วในรอบนี้

- เปลี่ยน default model ทั้งหมด → `gemma4:e4b` (cold-start ~16s เทียบ 51s ของ 12b)
  - `app/config.py`, `app/routers/config_router.py`, `app/services/perplexica_client.py`, `frontend/pages/settings.tsx`
- Patch Perplexica standalone JS ให้ wrap Ollama `getModelList()` live-check ใน try-catch
  (`loadChatModel` / `loadEmbeddingModel`) → network blip ชั่วคราวไม่ทำให้ search fail
  - แก้ใน `perplexica-src/.next/standalone/.next/server/chunks/136.js` (backup: `136.js.bak`)
  - แก้ source ด้วยที่ `perplexica-src/src/lib/models/providers/ollama/index.ts`

## ✅ A2 แก้แล้ว (รอบล่าสุด — ย้ายไป Groq) — สาเหตุ & วิธีแก้

**สาเหตุจริง:** เมื่อย้าย chat ของ A2 มาใช้ Groq (free) → Vane เรียกโมเดลด้วย **strict
`json_schema`** *และ* **tool-calling** (web_search) พร้อมกัน โมเดลที่ทำไม่ได้ทั้งคู่จะ
error/ค้างจน timeout แล้วลากให้ผลทั้งก้อนไม่ถูก save (เพราะ `save_result()` อยู่ท้าย pipeline)
- `openai/gpt-oss-120b` / `meta-llama/llama-4-scout` → tool_use_failed หรือไม่ค้น web
- `llama-3.3-70b` / `qwen3` → ไม่รองรับ json_schema (400)

**แก้:**
1. stage `llm.perplexica.model` → **`openai/gpt-oss-20b`** (ตัวเดียวที่ผ่านครบ + ได้ sources จริง)
2. `perplexica_client.build_query()` — ตัด quote/SQL/id/timestamp ออก → query สะอาด
   ทำให้ speed mode ค้น web จริง (ไม่งั้นโมเดลตอบจากความรู้เอง 0 sources)
3. เพิ่ม `perplexica.mode` (Vane optimizationMode) default **`speed`** — เสถียร + ได้ sources;
   `balanced`/`quality` ลึกกว่าแต่บนโมเดลฟรี Groq อาจค้างจาก tool-use loop
4. ลด `perplexica.timeout` **480s → 90s** (speed ตอบ ~20s/host)

verify ผ่าน pipeline จริง: A2 OK ใน ~24s, answer 5806 chars, **15 sources** (save 5)

> หมายเหตุ: ปม Ollama-CPU/480s เดิมไม่เกี่ยวแล้ว (chat ย้ายไป Groq, embedding ใช้
> Transformers ในเครื่อง ไม่พึ่ง Ollama) ถ้าจะใช้ quality mode ให้สลับ stage perplexica
> ไป provider ที่ tool-use เสถียรกว่า (เช่น Cerebras/OpenAI) แล้วค่อยเปิด

## ✅ แก้เพิ่มเติม (callback path)

- **logsim dashboard schema mismatch** — `AnalysisResultsPanel.tsx` อ่าน field ผิด
  (`r.id`/`r.aa_synthesis`/hosts เป็น object) → แสดง UNKNOWN/Invalid Date/A2 ไม่โผล่
  → เขียนใหม่ตาม AnalyzeResponse จริง (per-host synthesis + prediction + A2 enrichment)
- **default callback port 8000 → 8071** (logsim backend) ใน `SimulationDrawer.tsx`
- **`/ingest` 202 + background** — เดิม await pipeline เต็ม (รวม A2 ~7 นาที) ก่อนตอบ →
  logsim timeout 180s รายงาน "0 lines sent". แก้: ถ้ามี `callback_url` รัน background +
  ตอบ 202 ทันที ส่งผลทาง callback (`app/routers/ingest.py`)
- **one-command deploy:** `bash deploy.sh` (install+start ทุก service) — ดู README

## 🔧 setup เครื่องใหม่ — คำสั่งเดียว

```bash
git clone https://github.com/sumate001/aiops.git && cd aiops
export OLLAMA_BASE_URL=http://100.94.37.18:11434   # remote ที่มี gemma4:e4b
nvm use 22                                          # หรือ install ก่อน
bash deploy.sh        # install + start ทุก service → http://localhost:3002
```
`deploy.sh` เช็ค prereq (Python/Node≥18/Docker), ติดตั้ง deps, clone+build Vane,
start: SearXNG(4000) · log-ml(3050) · Perplexica(3001) · backend(8200) · frontend(3002)
แล้วรอ health เอง · `--status` / `--start` / `--stop` ใช้คุมต่อได้

- ✅ **Ollama patch อยู่ใน fork `sumate001/Vane` แล้ว** (commit 1e682a7) — build มาพร้อม bypass **ไม่ต้อง re-apply เอง**
- `config.yaml` (gitignored) สร้างจาก example อัตโนมัติ (e4b + timeout 480s + perplexica enabled) — แก้ `ollama.base_url` ตามจริง

## หมายเหตุ environment

- Ollama remote: `http://100.94.37.18:11434` (Tailscale) — มี `gemma4:e4b`, `gemma4:12b`, `nomic-embed-text:latest`
- gemma4:e4b cold-start ~16s, gemma4:12b ~51s
- Node 22 สำหรับ Perplexica + aiops frontend (Node 15 default ใช้ไม่ได้ — `node:crypto` error)
- pyenv 3.14.0 สำหรับ aiops/log-ml
- Ports: logsim FE 3200, logsim BE 8071, aiops FE 3002, aiops BE 8200, Perplexica 3001, SearXNG 4000, log-ml 3050
