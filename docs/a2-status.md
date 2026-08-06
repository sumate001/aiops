# A2 (Perplexica) — สถานะ ณ 2026-08-05

ผลตรวจตาม Phase 1 ของแผน godeyes — **ไม่มี code change** ในรอบนี้

**สรุปหนึ่งบรรทัด:** A2 ตอบ 200 และคืน source > 0 จริง แต่ค้นได้จาก Stack Overflow
เพียงเอนจินเดียว ส่วนที่เหลือของคำตอบ ~4000 ตัวอักษรเป็นความจำของ LLM ที่ไม่มีแหล่งอ้างอิง
และถูกส่งเข้า judge prompt ของ AA โดยไม่มีการเช็คจำนวน source

---

## 1. Config ที่ใช้จริง

| ค่า | สถานะ |
|---|---|
| `perplexica.enabled` | **true** |
| `perplexica.base_url` | `http://localhost:3001` (ตอบ 200) |
| `perplexica.mode` | `speed` |
| `perplexica.embedding_model` | `Xenova/all-MiniLM-L6-v2` (local Transformers) |
| A2 chat stage (`llm.perplexica`) | provider `groq`, model `openai/gpt-oss-20b`, override=true |
| `PERPLEXICA_TIMEOUT` | 90s |

Container/process ที่เกี่ยวข้อง:
- `aiops-searxng` — Up, `0.0.0.0:4000->8080/tcp` ตอบ 200
- Perplexica — ฟังที่ 3001 ตอบ 200 (`setupComplete = false` แต่ search ใช้งานได้)
- app — `uvicorn app.main:app` port **8200**

---

## 2. หลักฐาน: รัน POST /analyze หนึ่งรอบ

payload: 30 entries, host `pos-cluster-01`, service `mysql`, MySQL deadlock + connection pool exhausted

```
HTTP 200 in 250.8s
=== host: pos-cluster-01 ===
  status: critical  health: 0.0
  enrichment.answer:  4000 chars
  enrichment.sources: 1
     - https://stackoverflow.com/q/67985615
  enrichment.query: database slow query deadlock connection pool error deadlock
                    found when trying to get lock troubleshooting
  synthesis.confidence: 0.887
```

log ของ app ในรอบเดียวกัน:

```
INFO app.routers.analyze  === Phase 4: A2 Perplexica — sequential ===
INFO app.routers.analyze  A2 start — host=pos-cluster-01 query=database slow query deadlock connection pool error...
INFO app.services.perplexica_client  Perplexica search OK: 6344 chars, 1 sources
INFO app.routers.analyze  A2 OK — host=pos-cluster-01 answer_len=4000 sources=1
WARNING app.services.synthesizer  AA Synthesizer LLM failed for pos-cluster-01: llm timeout: — falling back to rule
```

**ไม่พบ** JSON parse error หรือ tool-call schema error จาก A2 stage
(ที่แผนคาดไว้) — A2 ไม่ได้พังที่ structured output

---

## 3. สาเหตุที่ source น้อย — SearXNG เหลือเอนจินเดียว

เรียก SearXNG ตรงๆ ด้วย query ชุดเดียวกับที่ `build_query()` สร้าง:

```
Q(2w)  "mysql deadlock"                    → 10 results | by engine: {'stackoverflow': 10}
Q(13w) "database mysql deadlock connection error deadlock found when trying to get lock troubleshooting"
                                            → 0 results  | by engine: {}
unresponsive (ทั้งสองรอบ):
  [['github','Suspended: access denied'], ['mojeek','Suspended: access denied'],
   ['qwant','Suspended: access denied'],  ['wikidata','Suspended: access denied']]
```

จาก `settings.yml` ใน volume ของ container (`/etc/searxng/settings.yml`):

| engine | ตั้งไว้ | สภาพจริงตอนรัน |
|---|---|---|
| duckduckgo | `disabled: true` | ปิดตั้งใจ (เคยโดน CAPTCHA/429 ban) |
| brave | `disabled: true` | ปิดตั้งใจ |
| startpage | `disabled: true` | ปิดตั้งใจ |
| mojeek | `disabled: false` | **Suspended: access denied** |
| qwant | `disabled: false` | **Suspended: access denied** |
| github | `disabled: false` | **Suspended: access denied** |
| wikidata | (default) | **Suspended: access denied** |
| wikipedia | `disabled: false` | ตอบได้ แต่ไม่เคยแมตช์ query ที่เป็น error string |
| **stackoverflow** | `disabled: false` | **เอนจินเดียวที่คืนผลจริง** |

ผลที่ตามมา 2 ข้อ:

1. **corpus ของ A2 = Stack Overflow เท่านั้น** — ปัญหาที่ไม่มีใครถามใน SO จะไม่มีหลักฐานเลย
2. **query ยิ่งยาวยิ่งได้ 0** — `build_query()` ต่อ frame + 3 keywords + error phrase 8 คำ
   + คำว่า "troubleshooting" ออกมาเป็น 13 คำ ซึ่ง matcher ของ SO แมตช์ไม่ติด
   (วัดได้: 2 คำ → 10-40 ผล, 8 คำ → 10 ผล, 13 คำ → 0 ผล)

ผลไม่นิ่ง: query 13 คำชุดเดียวกันรันคนละรอบได้ 0 บ้าง 1 บ้าง — เอนจินทยอยโดน suspend
ระหว่างวัน จำนวนผลจึงลดลงเรื่อยๆ

---

## 4. ความเสี่ยงที่พบเพิ่ม (ไม่ได้อยู่ใน scope Phase 1 แต่ควรรู้)

### 4.1 คำตอบที่ไม่มี source ถูกส่งเข้า AA โดยไม่มีการกรอง

`app/services/synthesizer.py:216`

```python
research_block = f"  {perplexica_answer}" if perplexica_answer else "  (no web research)"
```

เช็คแค่ว่า `answer` มีข้อความไหม — **ไม่ได้เช็คว่ามี source กี่อัน**
เมื่อ SearXNG คืน 0 ผล Perplexica จะให้ LLM เขียนตอบจากความจำแทน แล้ว A2 คืน
`answer` ยาว 4000 ตัวอักษรพร้อม `sources: []` ซึ่งไปโผล่ใน prompt ใต้หัวข้อ
"web research" เหมือนเป็นหลักฐานที่ค้นมาได้จริง

คำตอบที่ probe ได้มา ประกาศตัวเองด้วยซ้ำว่าไม่มีแหล่ง:

> *(All facts are derived from general MySQL documentation and common DBA experience;
> no specific source is available in the provided context.)*
> ... "**[no source]**" (ซ้ำท้ายเกือบทุกหัวข้อ)

นี่ขัดกับเป้าหมาย "root cause ที่ลึกและอิงหลักฐาน" โดยตรง — AA ถูกป้อน prose ที่
ฟังดูน่าเชื่อแต่ไม่มีอะไรรองรับ

### 4.2 AA LLM timeout ในรอบที่ทดสอบ

รอบนี้ AA judge timeout ที่ 120s แล้ว fallback ไป rule-based
→ `confidence: 0.887` ที่เห็นมาจาก rule path ไม่ใช่จาก LLM judge
→ /analyze รวม 250.8s

**สังเกตได้ครั้งเดียว** (backend.log มีข้อมูลตั้งแต่ restart ล่าสุด 2026-07-13
และมี /analyze รอบนี้รอบเดียว) — ยังสรุปไม่ได้ว่าเป็นปัญหาประจำ ต้องเก็บสถิติเพิ่ม

---

## 5. การตัดสินใจตามเกณฑ์ Phase 1.2

เกณฑ์ในแผนคือ "sources > 0 → A2 ทำงานได้" ซึ่งรอบนี้ **ผ่านแบบเฉียดฉิว (1 source)**
แต่เกณฑ์นี้ไม่ครอบคลุมกรณีที่เจอจริง คือ *ได้ source มา 1 อัน พ่วงกับ prose
ไร้แหล่งอ้างอิงอีก 4000 ตัวอักษร*

ทางเลือกที่มี (ยังไม่ได้ลงมือ — รอตัดสินใจ):

| ทางเลือก | ทำอะไร | ต้นทุน |
|---|---|---|
| **A. ปิด A2** | `perplexica.enabled: false` | ต่ำสุด, ตัด 250s → เร็วขึ้นมาก, เสีย SO hit ที่บางทีก็มีประโยชน์ |
| **B. คงไว้ + gate ด้วย source count** | ถ้า `len(sources) == 0` → ไม่ส่ง answer เข้า prompt | เล็ก (แก้ 2 บรรทัดใน analyze.py/synthesizer.py) ตัดความเสี่ยง 4.1 ได้ตรงจุด |
| **C. ซ่อม query** | `build_query()` สั้นลงเหลือ ~4-6 คำ | เล็ก แต่ได้ผลจำกัด เพราะ corpus ยังเหลือ SO เอนจินเดียว |
| **D. เปลี่ยน backend** | Perplexity Search API แทน Perplexica | ใหญ่สุด, มีค่าใช้จ่าย, ดู `aiops-a2-search-api-consideration` |

**ข้อเสนอ: B + C ควบกัน** (รวมกันไม่เกินครึ่งชั่วโมง) แล้วค่อยประเมิน D ทีหลัง
เหตุผลที่ยังไม่ปิดทิ้ง (A): SO hit ที่ได้มาเป็นหลักฐานจริงที่ตรงประเด็น และ
gate ตามข้อ B ทำให้กรณี 0 source ไม่เป็นพิษกับ AA อยู่แล้ว

### ผลหลังลงมือทำ B + C (2026-08-06) — เลือกทางเลือก B+C แล้ว

**C — `build_query()` ใหม่:** ข้อมูลจากการวัดหักล้างสมมติฐานแรก ปัญหาไม่ใช่แค่
"query ยาวไป" แต่เป็น **engine ที่เหลือ intersect terms** ทุกคำที่เพิ่มเข้าไปทำให้ผลหด
ไม่ใช่แคบลงอย่างมีคุณภาพ:

```
"deadlock"                             → 24 ผล
"slow query"                           → 13 ผล
"slow query deadlock troubleshooting"  →  0 ผล   ← สองวลีที่ดีทั้งคู่ พอรวมกันแล้วศูนย์
```

และคำว่า `troubleshooting` ที่ต่อท้ายทุก query เป็นตัวถ่วง ไม่เคยช่วยเลย:

```
"deadlock found lock"  → 10 ผล  |  + troubleshooting →  5
"kernel ext4 fs"       → 10 ผล  |  + troubleshooting →  0
"connection refused"   → 10 ผล  |  + troubleshooting → 10
```

จึงเปลี่ยนเป็น: **เลือกสัญญาณที่เจาะจงที่สุดมาอันเดียว จำกัด 3 คำ ไม่ต่อ suffix**
ลำดับ keyword (จาก mirofish) → error phrase → anomaly metric → ถ้าไม่มีเลยให้ข้าม A2

**ไม่ fallback ไปใช้ชื่อ frame เปล่าๆ อีกแล้ว** — `"hardware troubleshooting"` คืน 10 ผลก็จริง
แต่เป็นผลกว้างๆ ที่ไม่เกี่ยวกับ incident ซึ่ง *แย่กว่า* ไม่ค้นเลย เพราะมี URL จริงติดมาด้วย
เลยผ่าน source gate ข้อ B ไปได้ทั้งที่ไม่มีเนื้อหา

เทียบก่อน/หลัง (ยิงเข้า SearXNG จริง):

| frame | query เดิม | ผล | query ใหม่ | ผล |
|---|---|---|---|---|
| Database | `database slow query deadlock connection pool error deadlock found when trying to get lock troubleshooting` (13w) | 0 | `slow query` | 10 |
| Network | (13w) | 0 | `connection refused` | 10 |
| Hardware | `hardware troubleshooting` (fallback) | 40 กว้างๆ | `kernel ext4 fs` | 10 ตรงประเด็น |
| Security | (13w) | 0 | `auth failure` | 10 |
| Software (metrics only) | — | — | `cpu usage high` | 10 |
| frame อย่างเดียว | `hardware troubleshooting` | 40 กว้างๆ | `""` → ข้าม A2 | — |

**B — source gate:** `analyze.py::_phase5_aa_llm` ส่ง `perplexica_answer` เข้า judge
เฉพาะเมื่อ `st.enrichment.sources` ไม่ว่าง ถ้า 0 source จะ log
`A2 answer withheld from judge — host=… (0 sources)` แล้วไม่ส่งเข้า prompt
(ตัว `enrichment` ยังอยู่ใน response ตามเดิม เพื่อให้ UI เห็นว่า A2 รันแล้วได้อะไรกลับมา)

**ผล E2E ของ POST /analyze ชุดเดิม (payload เดียวกับข้อ 2):**

| | ก่อน | หลัง |
|---|---|---|
| A2 query | 13 คำ | `slow query` |
| `enrichment.sources` | 1 | **5** |
| AA judge | timeout 120s → fallback rule | **สำเร็จ** (`ollama_used: true`) |
| `root_cause_chain` | bullet ของ rule engine | causal chain จริง (slow query → lock hold → pool exhaustion → ERROR 1213) |
| confidence | 0.887 (rule) | 0.92 (LLM) |
| latency /analyze | 250.8s | **69.1s** |

**UI — ปิดช่องโหว่เดียวกันฝั่งคน (2026-08-06):** gate ข้อ B กัน prose ไร้แหล่งอ้างอิง
ออกจาก prompt ของ AA ได้ แต่ dashboard ยังโชว์ข้อความนั้นให้ "คน" อ่านเหมือนเดิม
ซึ่งอันตรายกว่า เพราะคนคือคนที่ลงมือแก้จริง จึงแก้เพิ่ม:

- `frontend/pages/index.tsx` — type `enrichment` ไม่มี field `sources` ด้วยซ้ำ
  คำตอบที่มีแหล่งกับไม่มีแหล่งจึงหน้าตาเหมือนกันเป๊ะ ตอนนี้เพิ่ม `sources` เข้า type แล้ว
  แสดง `Context · N sources` เมื่อมีแหล่ง และถ้า 0 แหล่งจะขึ้น `⚠ 0 sources — ไม่ได้ส่งให้ AA`
  พร้อมหรี่ตัวอักษรเป็นสีเทา italic
- `frontend/pages/pipeline.tsx` —
  - A2 panel: ถ้า 0 source กล่องคำตอบเปลี่ยนเป็นโทน amber + คำอธิบายว่าข้อความนี้
    โมเดลเขียนเองจากความจำ ไม่ใช่ผลค้น และถูกกันออกจาก prompt แล้ว
  - Sources แสดงจำนวน + คลิกเปิด URL ได้ (เดิมเป็น badge เฉยๆ กดไม่ได้)
  - AA panel: chip `enrichment (A2)` เปลี่ยนเป็นแบบขีดฆ่า `✕ withheld, 0 sources`
    เมื่อถูกกัน — ไดอะแกรมจะได้ไม่โกหกว่า A2 ป้อนเข้า AA เสมอ
  - A2 input chips: เดิมเขียนว่า `top_errors[] (query)` ซึ่งไม่ตรงแล้ว
    ตอนนี้สะท้อนลำดับจริงของ `build_query()`: mirofish keywords (A3) → top_errors (A1)
    → anomaly metrics (A1)
  - ข้อความตอนไม่มี enrichment เดิมเขียนว่า "Perplexica disabled" อย่างเดียว
    ทั้งที่ตอนนี้ข้ามได้อีกหลายเหตุ (ไม่มีสัญญาณที่ค้นได้ / timeout / cooldown) — แก้ให้ครบ

- `frontend/pages/index.tsx` — การ์ด AA เดิมเขียนว่า "รับ output จาก A1+A2+A3 ทั้งหมด"
  ซึ่งพูดเกินจริงเมื่อ A2 ถูกกัน ตอนนี้เปลี่ยนตามสถานะจริงเป็น
  `A1+A3+A2` หรือ `A1+A3 (A2 ถูกกัน: 0 sources)`

**ยืนยันด้วยภาพจริงแล้ว** (Playwright + chromium headless):
`npx tsc --noEmit` ผ่าน, `npm run build` ผ่าน, restart :3002 แล้ว และ screenshot
ครบทั้ง 4 สถานะ — `/pipeline` กับ `/` × (grounded / ungrounded)

เคส 0 source สร้างด้วยการ **intercept API response แล้วลบ `sources` ทิ้งกลางทาง**
(`page.route()`) ไม่ได้แก้ product code เพื่อให้ทดสอบได้ — bundle ที่ถูกทดสอบคือตัวจริงที่ deploy
สิ่งที่เห็นในภาพ:

| | grounded (5 sources) | ungrounded (0 sources) |
|---|---|---|
| pipeline · กล่อง answer | โทนเทาปกติ | โทน amber + `⚠ 0 sources — ไม่ถูกส่งให้ AA` + ตัวอักษรหรี่ |
| pipeline · Sources | `SOURCES (5)` กดเปิด URL ได้ | ไม่แสดง |
| pipeline · chip ของ AA | `↓ enrichment (A2)` | `✕ enrichment (A2) — withheld, 0 sources` ขีดฆ่า |
| dashboard · หัวข้อ | `Context · 5 sources` | `⚠ 0 sources — ไม่ได้ส่งให้ AA` |
| dashboard · การ์ด AA | `รับ output จาก A1+A3+A2` | `รับ output จาก A1+A3 (A2 ถูกกัน: 0 sources)` |

สคริปต์ที่ใช้: `/tmp/uiverify/ui_verify.mjs` (ต้อง symlink
`node_modules` ของ `perplexica-src` เข้ามา เพราะ playwright ติดตั้งอยู่ที่นั่น)

ข้อสังเกตที่ยังเหลือ (ไม่ได้แก้ในรอบนี้):
- `build_query` เลือก `top_keywords[0]` แบบตรงไปตรงมา รอบนี้ได้ `slow query`
  ทั้งที่ `deadlock` น่าจะตรงกว่า — ยังไม่มีการจัดอันดับว่า keyword ไหน "ค้นแล้วได้ผลดีกว่า"
- source ที่ได้มา 5 อันมี GitHub repo ที่เกี่ยวข้องหลวมๆ ปนมา (query กว้าง)
  gate ข้อ B เช็คแค่ "มี source ไหม" ไม่ได้เช็คคุณภาพ
- `_clean_error` ยังกลืน error code ที่มีตัวเลข (`ORA-01555` → `ora`, `1213` หาย)
  ซึ่งเป็น term ที่ค้นแล้วตรงที่สุด — **Phase 2.4 จะสร้าง `normalize.py`
  พร้อม whitelist (`ORA-\d+`, `errno \d+`, `HTTP \d{3}`) อยู่แล้ว ให้ไปใช้ตัวนั้นร่วมกัน**
  จะได้ไม่มีสองสูตร
- หน้า pipeline ดูได้แค่ผลล่าสุด ไม่มีทางเปิดผลย้อนหลัง — ถ้ารับ `?id=` ได้
  จะ debug ง่ายขึ้นมาก (และทำให้ตรวจ UI เคส 0 source ได้โดยไม่ต้องรอให้มันเกิดสดๆ)
- logic ตัดสิน "grounded" ถูกเขียนแยกกันสองที่ (`grounded()` ใน pipeline.tsx กับ
  inline check ใน index.tsx) เพราะสองไฟล์นิยาม type ของตัวเอง — ถ้าจะเพิ่มหน้าที่สาม
  ควรดึงขึ้นไปไว้ที่เดียว

---

## 6. สถานะ Phase 1.5 — ทำไปแล้วทั้งหมด

ตรวจ code จริงแล้วพบว่า 3 ข้อของ Phase 1.5 ในแผน **ถูกแก้ไปแล้ว** (แผนล้าสมัย):

| ข้อ | สถานะ | หลักฐาน |
|---|---|---|
| 1.5.1 persist window_stat หลัง IF | ✅ แล้ว | `analyze.py:183` `save_window_stat()` อยู่หลังบล็อก IF ที่ recompute `health_score` (บรรทัด 173) พร้อม comment อธิบายไว้ |
| 1.5.2 trend/prediction เข้า AA | ✅ แล้ว | `synthesize()` รับ `trend`, `prediction` แล้ว; output จริงมี `[Predictor] high risk — ~45 min` และ `[Predictor] Matched failure fingerprint` ใน `root_cause_chain` |
| 1.5.3 แยก confidence สองตัว | ✅ แล้ว | `response.py:17` เป็น `self_confidence` พร้อม comment ชี้ไป `Synthesis.confidence` |

→ **ข้ามไป Phase 2 (Qdrant memory) ได้เลย** ไม่ต้องทำ Phase 1.5 ซ้ำ

---

## 7. Acceptance criteria (Phase 1.3)

- [x] มีไฟล์ `docs/a2-status.md` ระบุสถานะ A2 พร้อมหลักฐาน (log + response ตัวอย่าง)
- [n/a] ยืนยันว่า /analyze ทำงานครบ pipeline เมื่อปิด A2 — เลือกทางเลือก B+C ไม่ได้ปิด A2
      (criterion นี้มีผลเฉพาะกรณีเลือกทางเลือก A)

Test ที่เพิ่ม: `tests/test_perplexica_query.py` (word budget, ไม่ต่อทุกสัญญาณ,
ไม่มี suffix, ลำดับ signal, frame เปล่าต้องข้าม) และ `tests/test_a2_source_gate.py`
(0 source → ไม่เข้า prompt, มี source → เข้า, enrichment ยังอยู่ใน response)
— `pytest tests/` ผ่าน 93 ข้อ

## วิธี reproduce

```bash
cd /home/ops/aiops
# A2 ผ่าน client จริง
.venv/bin/python /tmp/a2_probe.py
# SearXNG ตรงๆ ดู engine breakdown
curl -s "http://127.0.0.1:4000/search?q=mysql+deadlock&format=json" \
  | python3 -c "import json,sys,collections; d=json.load(sys.stdin); \
    print(collections.Counter(r['engine'] for r in d['results']), d['unresponsive_engines'])"
# /analyze เต็ม pipeline (port 8200)
.venv/bin/python /tmp/a2_analyze_probe.py
```
