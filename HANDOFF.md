# HANDOFF — งานที่ค้าง (2026-06-24)

สรุปสถานะเพื่อไปทำต่อบนเครื่องอื่น

## สถานะ pipeline ล่าสุด

| Stage | สถานะ | หมายเหตุ |
|-------|--------|----------|
| A1 Rule | ✅ ทำงาน | |
| A1 IF (log-ml) | ✅ ทำงาน | รันที่ port 3050 |
| A2 Perplexica | ✅ ทำงาน | verify ผ่าน pipeline จริง 265s (5 sources, answer 2000 ตัว) |
| A3 MiroFish | ✅ ทำงาน | Redis scenario → Software=100%, Hardware=20% |
| AA Synthesizer | ✅ ทำงาน | |

## ✅ เสร็จแล้วในรอบนี้

- เปลี่ยน default model ทั้งหมด → `gemma4:e4b` (cold-start ~16s เทียบ 51s ของ 12b)
  - `app/config.py`, `app/routers/config_router.py`, `app/services/perplexica_client.py`, `frontend/pages/settings.tsx`
- Patch Perplexica standalone JS ให้ wrap Ollama `getModelList()` live-check ใน try-catch
  (`loadChatModel` / `loadEmbeddingModel`) → network blip ชั่วคราวไม่ทำให้ search fail
  - แก้ใน `perplexica-src/.next/standalone/.next/server/chunks/136.js` (backup: `136.js.bak`)
  - แก้ source ด้วยที่ `perplexica-src/src/lib/models/providers/ollama/index.ts`

## ✅ A2 แก้แล้ว — สาเหตุ & วิธีแก้

**สาเหตุจริง:** remote Ollama (100.94.37.18) รัน inference บน **CPU** = ~9.8 tok/s
(วัดได้: load 11s, prompt eval 42 tok/s, gen 9.8 tok/s) Perplexica สร้างคำตอบ ~1500 tok
+ prompt eval ของ context 20 docs → รวม 225-300s+ เกิน timeout เดิม 300s

**แก้:** เพิ่ม `perplexica.timeout` → **480s** (ทั้ง `app/config.py` default และ `config.yaml`)
verify ผ่าน pipeline จริง: A2 OK ใน 265s, answer 2000 ตัว, 5 sources

**ถ้าจะเร่งให้เร็วขึ้น (อนาคต):** ย้าย Ollama ไปเครื่อง GPU, ตั้ง keep_alive กัน reload,
หรือลด num_ctx (ตอนนี้ 32000 ใน Perplexica standalone JS)

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
