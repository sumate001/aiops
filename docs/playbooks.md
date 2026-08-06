# Playbook Pack + Service Detection

> สถานะ: 2026-08-06 — ส่วนที่ไม่ติด Phase 2 ทำเสร็จแล้ว ที่เหลือรอ Qdrant
> ที่มา: แผนข้อ 2.11 (เดิมชื่อ "Knowledge Pack")

## ทำไมต้องมี

Phase 2-3 ทำให้ระบบเก่งขึ้นได้ก็ต่อเมื่อมีเคสสะสมและมีคนยืนยัน เดือนแรกที่ deploy
memory จะว่างเปล่า A4 ไม่ช่วยอะไรเลย — playbook คือความรู้ที่ ship มากับระบบ
ใช้ได้ตั้งแต่วันแรก

**ไม่ใช่การ fine-tune โมเดล** — เป็นการ seed ข้อมูลลง collection เดิม
ใช้ retrieval path เดียวกับ A4 ทุกประการ ไม่มีโมเดลใหม่ ไม่มี GPU เพิ่ม
อัปเดตได้โดยแก้ไฟล์ Python แล้วรัน seed ใหม่

## เรื่องชื่อ — ทำไมไม่เรียก "knowledge"

ในโปรเจกต์มีคำว่า knowledge อยู่ก่อนแล้ว 2 ความหมาย:

1. `app/knowledge/pos.py` — failure fingerprints ของ POS (ใช้ใน predictor)
2. `app/services/knowledge_store.py` + `GET /api/knowledge` — ผลวิจัยเบื้องหลัง
   ต่อ (software, version) ผ่าน Perplexica เก็บใน **SQLite**

ตัวใหม่นี้เก็บใน **Qdrant** และเป็นคนละเรื่องกับทั้งสองอัน ถ้าใช้ชื่อ knowledge
ซ้ำอีกจะแยกไม่ออกว่ากำลังพูดถึงอันไหน จึงเรียกว่า **playbook**
(`kind: "playbook"`, `playbook_refs`, `PLAYBOOK_VERSION`)

---

## สิ่งที่มีแล้ว

### `app/services/service_detector.py`

ตรวจว่า log ของ host มาจาก engine ไหน ด้วย regex ล้วน — ไม่ใช้ LLM ไม่โหลดโมเดล
เพราะรันทุก window

```python
detect_service(log_lines, sample=50, min_confidence=0.6) -> (engine | None, confidence)
resolve_service(explicit, log_lines, ...)  -> ใช้ค่าจาก ingest ก่อน ถ้ารู้จัก
normalize_service("MariaDB") -> "mysql"     # alias
```

**หลักที่ยึด: ยอมไม่รู้ ดีกว่าเดาผิด** — filter service ผิดจะซ่อน hit ที่ตรงที่สุดทิ้ง
ส่วนไม่ filter แค่ทำให้ค้นกว้างขึ้นเฉยๆ ต้นทุนสองแบบนี้ไม่เท่ากัน

confidence คิดจากสองอย่างคูณกัน:
- **dominance** — หลักฐานชี้ไป engine นี้เป็นสัดส่วนเท่าไรเทียบกับ engine อื่น
- **evidence** — มีบรรทัดที่ match มากพอไหม (ต้องถึง 3 บรรทัดถึงจะเต็ม 1.0)

ทำให้ log ที่เอ่ยถึง `mysqld` ผ่านๆ บรรทัดเดียวไม่ทำให้ทั้ง host ถูก filter ผิด

pattern แบ่งน้ำหนัก strong=3 / weak=1 — token ที่แทบไม่โผล่ใน engine อื่น
(`[MY-012345]`, `WiredTiger`) หนักกว่าคำที่แค่ใช้บ่อย (`mysqld`, `oplog`)

**กับดักที่ต้องระวัง:** PostgreSQL ใช้ `ERROR:`/`FATAL:` ซึ่งไปชนกับข้อความ
`FATAL ERROR: ... JavaScript heap out of memory` ของ Node (มีอยู่ใน logsim scenario จริง)
จึงบังคับให้ต้องมี `[pid]` นำหน้าตาม log_line_prefix มาตรฐาน `%m [%p]`
ไม่งั้นทุก Node OOM จะได้ playbook ของ database — มี test คุมไว้แล้ว

ผลไปอยู่ที่ `HostAnalysis.detected_service` (null ได้)
ต่อเข้า `_phase1_a1` แล้ว config อยู่ที่ block `service_detection:`

### `app/knowledge/playbooks/`

```
__init__.py      registry + validation
mysql.py         10 entries
postgresql.py    10 entries
mongodb.py       10 entries
```

`all_entries()` / `entries_for(engine)` / `available_engines()`
validate ทุกครั้งที่ดึงออก — entry ที่ผิดรูปจะ raise ทันที ไม่ปล่อยให้หลุดไป seed
(ของที่ลงไปใน persistent store แล้วแก้ยาก และมันจะไปโผล่เป็นคำแนะนำให้คนอ่าน)

**หัวข้อที่ครอบคลุมตอนนี้**

| engine | entries |
|---|---|
| mysql | lock wait timeout, deadlock, too many connections, disk full, replication lag, aborted connection, table corruption, slow query/full scan, max_allowed_packet, OOM killed |
| postgresql | deadlock, too many clients, WAL disk full, txid wraparound/autovacuum, idle in transaction, replication lag, statement timeout, connection refused, table bloat, OOM killed |
| mongodb | WiredTiger cache, replica set election, oplog window, connection pool, COLLSCAN, index build, disk full, CursorNotFound, write concern timeout, chunk migration |

### วิธีเพิ่ม entry

1. แก้ไฟล์ engine ที่ต้องการใน `app/knowledge/playbooks/`
2. `id` ต้องขึ้นต้นด้วยชื่อ engine (`mysql.xxx`) และไม่ซ้ำใคร
3. รัน `pytest tests/test_playbooks.py` — จะเช็คให้ว่า:
   - field ครบ, frame/severity อยู่ในชุดที่ยอมรับ
   - `symptom_text` ยาวพอ (≥15 คำ) — สั้นไปจะไม่ถูก retrieve
   - `fix_steps` มีคำสั่งที่รันได้จริง ไม่ใช่คำแนะนำลอยๆ
   - `verify_steps` มีอย่างน้อย 1 ข้อ
   - `error_codes` เป็น string (BM25 มอง `"1205"` กับ `1205` คนละ token)
4. bump `PLAYBOOK_VERSION` ถ้าแก้เนื้อหา
5. (เมื่อมี Phase 2) รัน `scripts/seed_playbooks.py`

**ข้อกำหนดเนื้อหา:** เขียนด้วยคำของเราเอง อย่า copy จากเอกสาร vendor
(ลิขสิทธิ์) ใส่ `docs_url` อ้างอิงแทน — `symptom_text` ต้องมีทั้งคำที่โผล่ใน log จริง
และคำอธิบายอาการแบบที่คนพูด เพราะจะถูก embed ไปเทียบกับ symptom_text
ที่สร้างจาก log จริง ถ้ามีแค่ด้านเดียวจะเสีย recall ไปครึ่งหนึ่ง

---

## ที่ยังทำไม่ได้ — ติด Phase 2

| ส่วนในแผน | ติดอะไร |
|---|---|
| 2.11.3 `scripts/seed_playbooks.py` | ต้องมี `memory_store` + Qdrant + embedder |
| 2.11.4 scoring / `kind_weight` 1.2 / `__global__` tenant | `memory_store.py` ยังไม่มี |
| 2.11.5 prompt แยก section KB | ต้องมี memory section จาก 2.7 ก่อน |
| 2.11.6 feedback ตอบ 409 กับ playbook | `feedback_router.py` (Phase 3) ยังไม่มี |
| 2.11.7 `memory.playbook_pack:` | ต้องมี block `memory:` ก่อน |
| 2.11.8 metrics `godeyes_kb_*` | รอ retrieval path |

**สิ่งที่ต้องไม่ลืมตอนทำ Phase 2:**

- `kind_weight`: verified analysis = 1.6 · **playbook = 1.2** · unverified analysis = 1.0
  เคสจริงบนเครื่องเราที่คนยืนยันแล้วมีค่ากว่าความรู้ทั่วไปเสมอ
  playbook มาเติมเมื่อไม่มีเคสจริง ไม่ใช่มาแทน
- `time_decay` และ `occurrence_boost` = 1.0 สำหรับ playbook (ความรู้ไม่เก่าตามวัน)
- กัน slot ให้ analysis อย่างน้อย 1 ถ้ามี — ไม่ให้ playbook กวาดหมดทุก slot
- **playbook hit ต้องไม่ขยับ confidence** เหมือน unverified analysis
  เพราะยังไม่มีหลักฐานว่าเกิดจริงบนเครื่องนี้
- feedback ห้ามแก้ point ที่เป็น playbook → 409 พร้อมบอกให้ไปแก้ไฟล์แล้ว re-seed
  (ถ้าปล่อยให้ทับ: tenant เดียวแก้แล้วทุก tenant เห็น เพราะอยู่ใต้ `__global__`
  แล้ว seed รอบหน้าจะทับกลับ ตีกันไม่จบ)
- คนที่อยากบอกว่า playbook ผิด ให้ใช้ deprecate ซึ่งเก็บ flag **แยกต่อ tenant**
  ในตาราง `playbook_overrides(tenant_id, point_id, deprecated, note)` ใน SQLite

---

## ยืนยันแล้ว

- `pytest tests/` — 120 ข้อผ่าน (เพิ่ม `test_service_detector.py` 12 ข้อ,
  `test_playbooks.py` 15 ข้อ)
- E2E ผ่าน `POST /analyze` จริงบน :8200 — ป้อน log 4 host พร้อมกัน
  โดยตั้ง `service: "db"` ให้ไม่มีประโยชน์ทั้งหมด:

```
OK  probe-mysql            mysql        confidence=1.00
OK  probe-postgresql       postgresql   confidence=1.00
OK  probe-mongodb          mongodb      confidence=1.00
OK  probe-__unknown__      None         confidence=0.00   ← ไม่เดา
```

test ที่ควรรู้ว่ามี: `test_playbooks.py::test_fix_steps_are_actionable`
เคยจับได้จริงว่า `mongodb.cursor_not_found` มีแต่คำแนะนำ ไม่มีคำสั่งที่รันได้
(แก้เนื้อหาแล้ว ไม่ได้ผ่อน test)
