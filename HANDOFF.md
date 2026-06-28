# HANDOFF — งานที่ค้าง (2026-06-24)

สรุปสถานะเพื่อไปทำต่อบนเครื่องอื่น

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
