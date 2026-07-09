# Proposal: Topology-aware Prediction Engine (draft สำหรับพิจารณา)

> สถานะ: **เริ่มพัฒนาแล้ว (2026-07-09)** — Phase 1 ลงมือแล้ว
> เอกสารนี้สรุปไอเดียเดิมที่เสนอ + ความเห็น/ข้อเสนอปรับ + บันทึกการตัดสินใจ

## 0. Decision log (2026-07-09)

- **เริ่ม dev เลย** บนสมมุติฐาน: GodEye ส่ง network + service topology เป็น JSON
  ผ่านจุด upload (`POST /topology`) — schema เปิด extra ไว้รอ format จริง
- **Engine ใหม่จะมาแทน A3 (MiroFish expert opinions)**: ตัดเฉพาะส่วน LLM
  enrichment (5 expert calls ที่ restate symptom) แต่**เก็บ frame scoring
  แบบ deterministic ไว้** เป็นตัวจำแนกสัญญาณป้อน "หัว" ของ simulator
  — สลับตอน Phase 3 ต่อผลเข้า synthesizer แล้วเทียบคุณภาพ `root_cause_chain`
  ก่อน/หลัง ไม่ถอด A3 ก่อนของใหม่พร้อม
- **Phase 2 เพิ่ม on-demand research**: ถ้าเจอปัญหาที่ไม่มีใน knowledge store
  ให้ยิง research ทันทีได้ (นอกเหนือจาก background queue) และ query ไม่จำกัด
  แค่ต่อ `(software, version)` เดี่ยวๆ — ครอบคลุม **ความไม่เข้ากันระหว่าง
  software กับ OS/version อื่นใน node เดียวกัน** ด้วย (compatibility research
  key = ชุด version ของ node)
- Phase 1 implemented: `app/models/topology.py`, `app/services/topology_store.py`,
  `app/routers/topology.py` (`POST /topology`, `GET /topology`,
  `GET /topology/node/{id}`) + `tests/test_topology.py`
- Phase 1.5: GodEye format adapter (`topology_adapter.py` — รับ
  `network_topology`/`service_dependency` ตรงๆ, สลับทิศ src/dst เป็น dependency,
  แยก os เป็น (name, version)) + ปุ่ม Browse file ในหน้า Settings
- Phase 2 implemented: `knowledge_store.py` (key ต่อ (software, version) +
  kind=compat สำหรับคู่ software×OS, backoff, stale TTL 30 วัน, bump_priority)
  + `research_worker.py` (1 query/4 นาที, พักคิว 15 นาทีเมื่อ upstream ล้ม,
  `research_now()` สำหรับ on-demand) — `GET /api/knowledge` ดูสถานะคิว
- Phase 3 implemented: `propagation.py` — graph propagation engine
  (deterministic, 8 แต้ม/นาที × weight × deficit, thresholds 70/50) วิ่งเป็น
  Phase 3.5 ใน analyze (เร็ว ไม่ขวาง SYNC); ผลออกทาง
  `AnalyzeResponse.propagation_forecast` (field ใหม่ ไม่กระทบ GodEye UI)
  และแนบเป็นหลักฐานเข้า judge prompt (propagation + version knowledge ต่อ host)
- ✅ ปิด LLM enrichment ของ A3 แล้ว (2026-07-09): เทียบ run จริง scenario เดียวกัน
  (result id 42 = A3 on vs id 43 = A3 off, tenant logsim, 6 hosts S1) —
  คุณภาพ `root_cause_chain` ไม่ตก (pgw-* เจาะจงขึ้น + confidence 0.6→0.8,
  lb/fw เทียบเท่า) และ pipeline เร็วขึ้นจากหลายนาทีเหลือ ~2.4s (ตัด 30 LLM calls)
  → toggle คือ `llm.mirofish.enabled` ใน config.yaml (false = ปิด expert calls,
  frame scoring แบบ deterministic ยังทำงานและยังป้อน judge/A2 query ตามเดิม)
  โค้ด LLM path ของ A3 ยังอยู่ เปิดกลับได้ทุกเมื่อ
- ข้อสังเกต: node role=external (VISA/ธนาคาร) โผล่ใน forecast ได้ —
  อาจกรองออกจากรายงานในอนาคต

---

## 1. ไอเดียตั้งต้น (ตามที่เสนอ)

เพิ่มฟังก์ชัน analyze แยกสำหรับ **Prediction โดยเฉพาะ** โดย:

**ข้อมูลรับเข้า:**
- Network topology
- Service topology
- Metrics
- Logs

**แนวทาง:**
1. แตก AI agent หนึ่งตัวต่อหนึ่ง node ตาม service topology — agent ทำตัวเสมือนเป็นเครื่องนั้น
2. แต่ละ agent ทำ research จาก internet เรื่อง version ของ Hardware / Software / OS ของตัวเอง
   เพื่อหาข้อบกพร่อง (known bugs/issues) ที่เคยมีคนโพสไว้ → เก็บเป็น memory ประจำ node
3. เมื่อรับข้อมูลจาก GodEye เข้ามา ให้ agent แต่ละ node ทำ **simulation** ตัวเองตาม topology
   โดยใส่ event จากข้อมูล GodEye แล้วดูว่าทั้ง topology จะเกิดอะไรขึ้นเมื่อเวลาผ่านไป
   **ทีละนาที** (แนว MiroFish)
4. เป้าหมายสุดท้าย: **Prediction** ว่าจะเกิด incident อะไร ที่ไหน เมื่อไหร่

---

## 2. สภาพปัจจุบันของ pipeline (ทำไมไอเดียนี้ถึงเป็น upgrade)

- การวิเคราะห์ทั้งหมดเป็น **รายเครื่อง (per-host)**: health score, MiroFish
  (`app/services/mirofish.py`), predictor (`app/services/predictor.py` — trend slope +
  failure fingerprint จาก POS knowledge base)
- **ไม่มีใครรู้ความสัมพันธ์ข้าม host** — db ล่มแล้วใครโดนหางเลข ระบบตอบไม่ได้
- มี web enrichment อยู่แล้ว (`perplexica_client.py` — cache 6 ชม. + cooldown 60s,
  SearXNG engine บางตัวถูกปิดใน container config)

→ ชิ้นที่ขาดจริงๆ และมีค่าที่สุดคือ **topology** — เป็นรากของทั้ง propagation และ prediction ข้าม host

---

## 3. ความเห็นต่อแต่ละส่วน

### 3.1 รับ topology เข้ามา — ✅ เห็นด้วยเต็มที่ (ชิ้นที่มีค่าที่สุด)

แค่มี dependency graph ก็ทำ failure propagation ได้ทันที (db ล่ม → app ที่ depend มี
risk สูงขึ้น) ซึ่งเป็น prediction ที่ของเดิมทำไม่ได้เลย ควรเป็นก้าวแรก

### 3.2 Research known issues ตาม version — ✅ เห็นด้วย แต่ปรับหน่วยเก็บ

- **Key ตาม `(software, version)` ไม่ใช่ต่อ node** — MySQL 8.0.32 มี 20 เครื่อง
  research ครั้งเดียวแล้วแชร์กัน node ถือแค่ pointer ไปยัง profile นั้น
- ทำเป็น **batch offline** ตอนรับ/อัปเดต topology หรือตอน version เปลี่ยน —
  **ไม่ทำตอน ingest** (ช้า + ชน rate limit ฝั่ง SearXNG)
- ต่อยอดจาก `perplexica_client.py` ที่มี cache/cooldown อยู่แล้ว

#### 3.2.1 เก็บแบบ incremental (ค่อยๆ สะสม) แทน batch ใหญ่ทีเดียว — ✅ แนะนำ

เพื่อเลี่ยงการโดนบล็อคฝั่ง Perplexica/SearXNG ไม่ต้อง research ทั้ง topology รวดเดียว
ใช้ **background research queue**:

- **แยก "การมีความรู้" ออกจาก "การหาความรู้"** — ตัว analyze ใช้ knowledge เท่าที่มี
  profile ที่ยังไม่มีข้อมูลก็วิเคราะห์ได้ตามปกติ (degrade gracefully)
- **Worker ตัวเดียว** ระบายคิวช้าๆ (เช่น 1 query / 3–5 นาที) — topology 30 node
  ยุบเหลือ ~10 unique `(software, version)` profile → เก็บครบใน ~1 ชม. โดยไม่มี burst
- **จัดลำดับความสำคัญ**: profile ของ node ที่กำลังมี anomaly/error ขึ้นหัวคิวก่อน
- **Persist ลง disk (ไฟล์/SQLite)** — cache ปัจจุบันใน `perplexica_client.py` เป็น
  in-memory (TTL 6 ชม.) restart แล้วหาย ความรู้สะสมต้องรอด restart
- **Backoff เมื่อ fail**: query โดนบล็อค/timeout → พักทั้งคิว 15–30 นาที ค่อยลองใหม่
  (cooldown 60s เดิมกันแค่ burst ใน request เดียว ไม่กันคิวยาวยิงต่อเนื่อง)
- **Staleness TTL รายเดือน**: known bugs ต่อ version ไม่เปลี่ยนรายชั่วโมง —
  re-research เฉพาะตอนครบอายุหรือ version เปลี่ยน → query ต่อวันในระยะยาวต่ำมาก

ผลพลอยได้: ระบบฉลาดขึ้นเรื่อยๆ เอง ไม่ต้องมี setup job ใหญ่ และ node ใหม่ที่เพิ่ม
เข้า topology ทีหลังก็แค่เข้าคิวตามปกติ

### 3.3 LLM agent ต่อ node + simulation รายนาที — ⚠️ จุดเสี่ยงสุด แนะนำปรับรูปแบบ

เหตุผล 3 ข้อ:

1. **LLM ไม่ใช่ simulator ที่ดี** — มัน "เล่าเรื่อง" cascade ไม่ใช่คำนวณจริง
   ผลออกมาฟังดูมั่นใจแต่ตรวจสอบไม่ได้ และ run ซ้ำได้ผลไม่เหมือนเดิม
   (non-deterministic) → backtest ไม่ได้ ซึ่งจำเป็นมากสำหรับงาน prediction
2. **ต้นทุนระเบิด** — N node × T นาที × LLM call ต่อ step
   (เช่น 15 node × 30 นาที = หลักร้อย call ต่อ 1 batch ingest)
3. **ชนกับโหมด SYNC** — GodEye รอคำตอบภายใน 60–120s simulation แบบนี้จบไม่ทัน

### 3.4 ทางเลือกที่แนะนำ: แยก simulator ออกจาก LLM

ตัว simulation เป็น **graph propagation engine ธรรมดา (deterministic, โค้ดล้วน)**:

- node แต่ละตัวมี state: health, load, latency
- edge มีกติกาการลาม: เช่น "db latency ขึ้น → app timeout ตาม delay X นาที น้ำหนัก Y"
- step ทีละนาทีด้วยการคำนวณ → เร็ว, ทำซ้ำได้, จูนพารามิเตอร์ได้, backtest ได้

LLM ใช้แค่ **หัว** กับ **ท้าย**:

- **หัว**: แปลง event จาก GodEye + knowledge ประจำ version (ข้อ 3.2) →
  พารามิเตอร์เริ่มต้นของ simulation ("MySQL version นี้มี known bug เรื่อง
  connection leak → ใส่ failure mode นี้เข้าไป")
- **ท้าย**: อ่านผล simulation → สรุป root cause + prediction + คำแนะนำ
  (แนวเดียวกับ synthesizer ปัจจุบัน)

แบบนี้ยังได้ spirit "แต่ละ node เสมือนเครื่องจริงตาม topology" ครบ —
ความรู้ของ agent ไปอยู่ใน **node profile** (version, known issues, dependencies)
แทน LLM instance ที่ต้องจ่ายทุก step และผล prediction
("อีก 12 นาที app-01 จะเริ่ม timeout") มาจากการคำนวณที่อธิบายได้จริง

---

## 4. ลำดับการทำที่แนะนำ (phased)

| Phase | งาน | หมายเหตุ |
|---|---|---|
| 1 | Schema รับ topology (endpoint ใหม่หรือขา ingest เพิ่ม) + เก็บ node profile | รากของทุกอย่าง |
| 2 | Version research แบบ batch → knowledge store ต่อ `(software, version)` | ต่อยอด perplexica_client |
| 3 | Graph propagation engine + ต่อผลเข้า `prediction`/`synthesis` ใน AnalyzeResponse เดิม | หัวใจของ prediction ใหม่ |
| 4 | (เผื่ออนาคต) per-node LLM agent เป็น layer เสริม เฉพาะเคสที่ rule-based ตอบไม่ได้ | อย่าเริ่มจากตรงนี้ |

**ข้อกำหนดสำคัญ:** งาน simulation ต้องวิ่งเป็น **background** แล้วส่งผลทาง async
callback หรือ `GET /api/results` — **ห้ามขวางเส้นทาง SYNC เดิม** ที่ GodEye ใช้อยู่

---

## 5. คำถามที่ต้องตอบก่อนเริ่ม

- [ ] GodEye ส่ง topology มาได้ในรูปแบบไหน? (format, ความถี่ในการอัปเดต, มี version
      ของ HW/SW/OS ต่อ node มาด้วยหรือไม่)
- [ ] Network topology กับ service topology มาแยกกันหรือรวมกัน? ใช้อะไรเป็น node id
      ให้ match กับ `host` ใน log/metric?
- [ ] กติกาการลามบน edge (delay/น้ำหนัก) เริ่มจาก preset ตาม service type
      แล้วค่อยจูน หรือจะให้เรียนรู้จาก incident จริงย้อนหลัง?
- [ ] ผล prediction ใหม่ควรอยู่ใน `AnalyzeResponse.prediction` เดิม หรือเพิ่ม field
      ใหม่ (เช่น `propagation_forecast`) เพื่อไม่กระทบ GodEye UI?
