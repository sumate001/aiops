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

## 🔧 สิ่งที่ต้องทำตอน setup เครื่องใหม่

1. รัน `setup.sh` → clone Perplexica จาก fork `sumate001/Vane` + start SearXNG docker (port 4000)
2. สร้าง `config.yaml` (gitignored) — copy จากเครื่องเดิม หรือใช้ default จากโค้ด (เป็น e4b แล้ว)
3. **Re-apply Perplexica Ollama patch** (จะหายเพราะ deploy clone สด):
   - แก้ `loadChatModel` / `loadEmbeddingModel` ใน chunk JS ให้ wrap live-check ด้วย try-catch
   - ดู diff ได้จาก `perplexica-src/.next/standalone/.next/server/chunks/136.js.bak` (ของเดิม)
4. Start services:
   - log-ml: `cd aiops/log-ml && ~/.pyenv/versions/3.14.0/bin/uvicorn app.main:app --host 0.0.0.0 --port 3050`
   - aiops backend: port 8200
   - aiops frontend: port 3002 (Node 22)
   - Perplexica: port 3001 (Node 22, `OLLAMA_BASE_URL=http://100.94.37.18:11434`, `SEARXNG_API_URL=http://localhost:4000`, `DATA_DIR=<repo>/perplexica-src`)

## หมายเหตุ environment

- Ollama remote: `http://100.94.37.18:11434` (Tailscale) — มี `gemma4:e4b`, `gemma4:12b`, `nomic-embed-text:latest`
- gemma4:e4b cold-start ~16s, gemma4:12b ~51s
- Node 22 สำหรับ Perplexica + aiops frontend (Node 15 default ใช้ไม่ได้ — `node:crypto` error)
- pyenv 3.14.0 สำหรับ aiops/log-ml
- Ports: logsim FE 3200, logsim BE 8071, aiops FE 3002, aiops BE 8200, Perplexica 3001, SearXNG 4000, log-ml 3050
