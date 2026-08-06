"""MongoDB playbooks — cold-start knowledge for A4.

Written from operational experience rather than copied from the manual; see
`docs_url` for the authoritative reference. See mysql.py for why symptom_text
mixes log tokens with plain description.
"""
from __future__ import annotations

PLAYBOOK_ENTRIES: list[dict] = [
    {
        "id": "mongodb.wiredtiger_cache_pressure",
        "engine": "mongodb",
        "frame": "Database",
        "severity": "high",
        "title": "WiredTiger cache เต็ม / eviction ตามไม่ทัน",
        "symptom_text": (
            "mongodb WiredTiger cache eviction stuck cache full application threads "
            "cache_overflow dirty bytes exceed latency spike slow operations "
            "cache เต็มจน thread ของ application ต้องมาช่วย evict เอง"
        ),
        "error_codes": [],
        "root_cause_chain": [
            "working set ใหญ่กว่า WiredTiger cache ทำให้ต้อง evict ตลอดเวลา",
            "เมื่อ dirty data เกินเพดาน MongoDB จะดึง thread ของ application มาช่วย evict ซึ่งทำให้ latency พุ่งทันที",
            "ต้นตอมักเป็น query ที่อ่านข้อมูลมากเกินจำเป็น (ไม่มี index) จึงดูดข้อมูลเข้า cache จนเบียดของที่ใช้จริงออกไป",
        ],
        "verify_steps": [
            "db.serverStatus().wiredTiger.cache — ดู 'bytes currently in the cache' เทียบกับ 'maximum bytes configured'",
            "ค่า 'tracked dirty bytes in the cache' ที่เข้าใกล้ 20% ของ cache คือกำลังจะมีปัญหา",
        ],
        "fix_steps": [
            "db.serverStatus().wiredTiger.cache — ดูสัดส่วน bytes in cache ต่อ maximum configured",
            "db.currentOp({'secs_running': {$gt: 5}}) — หา operation ที่กินยาว มักเป็นตัวดูดข้อมูลเข้า cache",
            "หา query ที่ไม่ใช้ index: db.setProfilingLevel(1, {slowms: 100}) แล้วดู db.system.profile หา planSummary: COLLSCAN",
            "เพิ่ม index ให้ query เหล่านั้น — ลดปริมาณข้อมูลที่ต้องผ่าน cache ได้ผลกว่าการเพิ่ม cache",
            "ปรับ wiredTigerCacheSizeGB ได้ แต่อย่าเกิน 50-60% ของ RAM เพราะ MongoDB ยังต้องใช้ page cache ของ OS ด้วย",
        ],
        "docs_url": "https://www.mongodb.com/docs/manual/core/wiredtiger/",
        "applies_to": ">=4.4",
    },
    {
        "id": "mongodb.replica_set_election",
        "engine": "mongodb",
        "frame": "Database",
        "severity": "critical",
        "title": "Replica set election / ไม่มี primary",
        "symptom_text": (
            "mongodb replica set election no primary available not master "
            "NotWritablePrimary PRIMARY stepped down heartbeat failed "
            "electionTimeoutMillis write ไม่ได้เพราะไม่มี primary"
        ),
        "error_codes": ["10107", "189", "91"],
        "root_cause_chain": [
            "สมาชิกใน replica set ติดต่อ primary ไม่ได้เกิน electionTimeoutMillis จึงเริ่มเลือกตั้งใหม่",
            "ระหว่างเลือกตั้งจะไม่มี primary ทำให้ write ทั้งหมดล้มเหลวชั่วคราว",
            "ถ้าเลือกตั้งวนไม่จบ มักเป็นเพราะจำนวนสมาชิกที่ยังติดต่อกันได้ไม่ถึงกึ่งหนึ่ง (ไม่ครบ majority) หรือเครือข่ายระหว่าง node ไม่เสถียร",
        ],
        "verify_steps": [
            "rs.status() — ดู stateStr ของทุกสมาชิก ต้องมี PRIMARY หนึ่งตัวและ SECONDARY ที่ health: 1",
            "ถ้าเห็นแต่ SECONDARY กับ (not reachable/healthy) แปลว่าไม่ครบ majority",
        ],
        "fix_steps": [
            "rs.status() — ดูว่าสมาชิกกี่ตัวยังติดต่อกันได้ ต้องเกินกึ่งหนึ่งถึงจะเลือก primary ได้",
            "ตรวจเครือข่ายระหว่าง node: ping และ telnet ที่พอร์ต 27017 ให้ครบทุกคู่ ไม่ใช่แค่จากเครื่องที่เรา ssh อยู่",
            "ดู log ของสมาชิกที่หายไปหา 'Heartbeat failed' พร้อมสาเหตุจริง",
            "ถ้าเสียไปหลายตัวจนไม่ครบ majority: ต้องกู้ node ที่เสียกลับมาก่อน อย่าใช้ force reconfig ถ้ายังมีทางอื่น เพราะเสี่ยงข้อมูลแตกสองทาง",
            "ตรวจว่านาฬิกาของทุก node ตรงกัน (chrony/ntp) — clock skew ทำให้ heartbeat เพี้ยนได้",
            "ให้ driver ฝั่ง application retry write ได้ (retryWrites=true) เพื่อให้ผ่านช่วงเลือกตั้งสั้นๆ ไปได้เอง",
        ],
        "docs_url": "https://www.mongodb.com/docs/manual/core/replica-set-elections/",
        "applies_to": ">=4.4",
    },
    {
        "id": "mongodb.oplog_window_too_small",
        "engine": "mongodb",
        "frame": "Database",
        "severity": "high",
        "title": "Oplog window สั้นเกิน — secondary กลายเป็น stale",
        "symptom_text": (
            "mongodb oplog window too small secondary too stale to catch up "
            "RECOVERING initial sync required oplog has been overwritten "
            "secondary ตามไม่ทันจน oplog ถูกเขียนทับ ต้อง resync ใหม่"
        ),
        "error_codes": ["133"],
        "root_cause_chain": [
            "oplog เป็น capped collection ขนาดคงที่ ของเก่าถูกเขียนทับเมื่อเต็ม",
            "ถ้า secondary ตามช้ากว่าที่ oplog หมุนครบรอบ จุดที่มันค้างอยู่จะถูกทับไปแล้ว ตามต่อไม่ได้",
            "สมาชิกตัวนั้นจะเข้าสถานะ RECOVERING และต้องทำ initial sync ใหม่ทั้งชุด ซึ่งกินเวลานานและกินแบนด์วิดท์",
        ],
        "verify_steps": [
            "rs.printReplicationInfo() — ดู 'oplog first event time' ถึง 'last event time' คือความยาว window จริง",
            "rs.printSecondaryReplicationInfo() — ดูว่า secondary ตามหลังกี่วินาที เทียบกับ window ข้างบน",
        ],
        "fix_steps": [
            "rs.printReplicationInfo() — ถ้า window สั้นกว่าเวลาที่ใช้ทำ maintenance หรือ backup แปลว่าตั้งไว้เล็กเกินไปตั้งแต่ต้น",
            "ขยาย oplog (ทำได้ตอนรัน ไม่ต้อง restart): db.adminCommand({replSetResizeOplog: 1, size: 51200})",
            "หาสาเหตุที่ secondary ช้า: I/O ของเครื่องนั้น, index build ที่กำลังรัน, หรือแบนด์วิดท์ระหว่าง node",
            "ถ้าสมาชิกเข้า RECOVERING ไปแล้ว ต้องทำ initial sync ใหม่ — ควรทำนอกเวลาใช้งานเพราะกินทรัพยากรทั้ง primary และเครือข่าย",
            "ตั้ง window ให้ยาวกว่างาน maintenance ที่ยาวที่สุดอย่างน้อยสองเท่า จะได้ไม่ต้องมาเจอซ้ำ",
        ],
        "docs_url": "https://www.mongodb.com/docs/manual/core/replica-set-oplog/",
        "applies_to": ">=4.4",
    },
    {
        "id": "mongodb.connection_pool_exhausted",
        "engine": "mongodb",
        "frame": "Database",
        "severity": "critical",
        "title": "Connection pool เต็ม",
        "symptom_text": (
            "mongodb connection pool exhausted waitQueueTimeoutMS timed out "
            "maxPoolSize connections created too many open connections "
            "Connection refused because too many open connections "
            "รอ connection จาก pool จนหมดเวลา"
        ),
        "error_codes": ["6", "89"],
        "root_cause_chain": [
            "จำนวน operation ที่ค้างอยู่มากกว่า maxPoolSize ของ driver คำขอใหม่จึงต้องรอคิว",
            "เมื่อรอเกิน waitQueueTimeoutMS จะล้มเหลวทั้งที่ database ยังรับงานได้",
            "ต้นตอเกือบทุกครั้งไม่ใช่ pool เล็กเกิน แต่เป็น operation ที่ช้าจนไม่คืน connection ให้ pool ทันเวลา",
        ],
        "verify_steps": [
            "db.serverStatus().connections — เทียบ current กับ available",
            "db.currentOp({'secs_running': {$gt: 1}}) — ถ้ามี operation ค้างเยอะ ปัญหาอยู่ที่ความช้า ไม่ใช่ขนาด pool",
        ],
        "fix_steps": [
            "db.serverStatus().connections — ดู current, available, totalCreated; ถ้า totalCreated โตเร็วผิดปกติแปลว่า connection ถูกสร้างใหม่ทิ้งๆ ขว้างๆ",
            "db.currentOp({'secs_running': {$gt: 1}}) — หา operation ที่ค้าง แล้วไล่ว่าทำไมช้า",
            "ตรวจว่า application สร้าง MongoClient ตัวเดียวใช้ร่วมกันทั้ง process — การสร้างใหม่ทุก request คือสาเหตุคลาสสิกของ pool ระเบิด",
            "แก้ query ที่ช้าก่อน (เพิ่ม index) แล้วค่อยพิจารณาเพิ่ม maxPoolSize — เพิ่ม pool อย่างเดียวแค่ย้ายคอขวด",
            "ตรวจ ulimit -n ของ process mongod — ถ้าเพดาน file descriptor ต่ำ จะรับ connection ได้น้อยกว่าที่ตั้งใจ",
        ],
        "docs_url": "https://www.mongodb.com/docs/manual/administration/connection-pool-overview/",
        "applies_to": ">=4.4",
    },
    {
        "id": "mongodb.collscan_slow_query",
        "engine": "mongodb",
        "frame": "Database",
        "severity": "medium",
        "title": "Query ช้าเพราะ collection scan",
        "symptom_text": (
            "mongodb slow query COLLSCAN planSummary docsExamined keysExamined "
            "query targeting ratio no index used slow operation ms "
            "query ช้าเพราะสแกนทั้ง collection ไม่ได้ใช้ index"
        ),
        "error_codes": [],
        "root_cause_chain": [
            "query ไม่มี index รองรับ MongoDB จึงต้องอ่านทุก document ใน collection",
            "เวลาตอบสนองโตตามขนาดข้อมูล และดูดข้อมูลที่ไม่เกี่ยวข้องเข้า WiredTiger cache จนเบียดของที่ใช้จริงออก",
            "ผลกระทบจึงลามไปที่ query อื่นที่เคยเร็ว ไม่ได้จำกัดอยู่แค่ query ที่ช้า",
        ],
        "verify_steps": [
            "db.<coll>.find(<filter>).explain('executionStats') — ถ้า planSummary เป็น COLLSCAN คือไม่ได้ใช้ index",
            "เทียบ docsExamined กับ nReturned — ต่างกันหลายเท่าคืออ่านเกินความจำเป็น",
        ],
        "fix_steps": [
            "เปิด profiler: db.setProfilingLevel(1, {slowms: 100})",
            "db.system.profile.find({planSummary: 'COLLSCAN'}).sort({millis: -1}).limit(10) — หา query ที่แย่ที่สุดก่อน",
            "db.<coll>.explain('executionStats').find(<filter>) — ดู docsExamined เทียบ nReturned",
            "สร้าง compound index ตามลำดับ equality → sort → range (ESR) — ลำดับผิดทำให้ index ถูกใช้ได้ไม่เต็มที่",
            "explain ซ้ำหลังสร้าง index เพื่อยืนยันว่า planSummary เปลี่ยนเป็น IXSCAN จริง",
            "สร้าง index บน production ด้วย background build เสมอ เพื่อไม่ให้ล็อกทั้ง collection",
        ],
        "docs_url": "https://www.mongodb.com/docs/manual/tutorial/analyze-query-plan/",
        "applies_to": ">=4.4",
    },
    {
        "id": "mongodb.index_build_blocking",
        "engine": "mongodb",
        "frame": "Database",
        "severity": "high",
        "title": "Index build กินทรัพยากร / บล็อกงานอื่น",
        "symptom_text": (
            "mongodb index build in progress createIndexes blocking "
            "index build percent complete resource intensive replication lag during build "
            "สร้าง index อยู่ ทำให้ระบบช้าและ secondary ตามไม่ทัน"
        ),
        "error_codes": [],
        "root_cause_chain": [
            "การสร้าง index ต้องอ่านทุก document ใน collection จึงกิน I/O และ cache หนัก",
            "บน replica set การ build จะเกิดบนทุกสมาชิก ทำให้ secondary ช้าลงพร้อมกันและ oplog window หดตัว",
            "ถ้า oplog หดจนสั้นกว่าที่ secondary ต้องใช้ตาม จะลามไปเป็นปัญหา stale secondary ต่อ",
        ],
        "verify_steps": [
            "db.currentOp({'command.createIndexes': {$exists: true}}) — ดูว่ากำลัง build อยู่จริงและถึงไหนแล้ว",
            "rs.printSecondaryReplicationInfo() ระหว่าง build — ดูว่า lag โตขึ้นหรือไม่",
        ],
        "fix_steps": [
            "db.currentOp({'command.createIndexes': {$exists: true}}) — ยืนยันว่า build กำลังรันและดูความคืบหน้า",
            "ถ้าจำเป็นต้องหยุด: db.adminCommand({dropIndexes: '<coll>', index: '<index_name>'}) จะยกเลิก build ที่ค้างอยู่",
            "เฝ้า replication lag ระหว่าง build — ถ้าโตเร็ว ให้เตรียมขยาย oplog ก่อนที่ secondary จะ stale",
            "จัด index build ไว้นอกช่วงพีค และทำทีละตัว ไม่ใช่หลายตัวพร้อมกัน",
            "บน sharded cluster ให้ทำทีละ shard เพื่อไม่ให้ทั้งคลัสเตอร์ช้าพร้อมกัน",
        ],
        "docs_url": "https://www.mongodb.com/docs/manual/core/index-creation/",
        "applies_to": ">=4.4",
    },
    {
        "id": "mongodb.disk_full",
        "engine": "mongodb",
        "frame": "Hardware",
        "severity": "critical",
        "title": "ดิสก์เต็ม",
        "symptom_text": (
            "mongodb No space left on device disk full WiredTiger error 28 "
            "cannot write journal shutting down dbPath full "
            "ดิสก์เต็ม mongod เขียนไม่ได้และปิดตัวเอง"
        ),
        "error_codes": ["28"],
        "root_cause_chain": [
            "พาร์ทิชันที่เก็บ dbPath เต็ม WiredTiger เขียน journal หรือ data file ไม่ได้",
            "mongod จะปิดตัวเองเพื่อกันข้อมูลเสียหาย",
            "สาเหตุที่พบบ่อยคือพื้นที่ที่ลบ document ไปแล้วไม่ถูกคืนให้ระบบไฟล์โดยอัตโนมัติ, log ที่ไม่หมุน, หรือ oplog ที่ขยายไว้ใหญ่",
        ],
        "verify_steps": [
            "df -h <dbPath> — ยืนยันว่าเต็มจริง",
            "du -sh <dbPath>/* | sort -h | tail — ดูว่าอะไรกินที่มากที่สุด",
        ],
        "fix_steps": [
            "df -h และ du -sh <dbPath>/* | sort -h | tail — หาตัวที่กินที่",
            "ตรวจ log ก่อนอย่างอื่น: mongod.log ที่ไม่หมุนโตได้เป็นสิบ GB — ตั้ง logRotate แล้วลบของเก่า",
            "ถ้าเป็น data file: db.runCommand({compact: '<collection>'}) คืนที่ให้ระบบไฟล์ได้ แต่ล็อก collection ระหว่างทำ — ทำบน secondary ทีละตัวแล้วสลับ",
            "ตรวจขนาด oplog: db.getReplicationInfo() — ถ้าเคยขยายไว้ใหญ่เกินจำเป็นสามารถลดลงได้",
            "หลังมีที่ว่างแล้วให้ start mongod และตรวจว่า rs.status() กลับมาปกติทุกสมาชิก",
        ],
        "docs_url": "https://www.mongodb.com/docs/manual/administration/production-notes/",
        "applies_to": ">=4.4",
    },
    {
        "id": "mongodb.cursor_not_found",
        "engine": "mongodb",
        "frame": "Software",
        "severity": "medium",
        "title": "CursorNotFound ระหว่างวนอ่านผลลัพธ์",
        "symptom_text": (
            "mongodb CursorNotFound cursor id not valid at server cursorTimeoutMillis "
            "getMore failed cursor expired batch iteration long running loop "
            "cursor หมดอายุกลางทางระหว่างวน loop อ่านข้อมูล"
        ),
        "error_codes": ["43"],
        "root_cause_chain": [
            "cursor ที่ไม่ถูกใช้งานเกิน cursorTimeoutMillis (ค่าเริ่มต้น 10 นาที) จะถูกเซิร์ฟเวอร์เก็บทิ้ง",
            "การเรียก getMore ครั้งถัดไปจึงล้มเหลว ทั้งที่อ่านไปได้แล้วครึ่งทาง",
            "เกิดจากรูปแบบการเขียนโค้ดที่ประมวลผลหนักอยู่ในลูปที่วน cursor ทำให้เว้นช่วงระหว่าง batch นานเกินไป",
        ],
        "verify_steps": [
            "ดู log ฝั่ง application ว่า error เกิดหลังเริ่มวนไปแล้วสักพัก ไม่ใช่ตั้งแต่ batch แรก",
            "db.serverStatus().metrics.cursor.timedOut — ถ้าค่าเพิ่มขึ้นเรื่อยๆ ยืนยันว่าเป็นการหมดอายุจริง",
        ],
        "fix_steps": [
            "db.serverStatus().metrics.cursor.timedOut — ยืนยันก่อนว่าเป็นการหมดอายุจริง ไม่ใช่ cursor ถูกปิดไปแล้ว",
            "db.currentOp({type: 'idleCursor'}) — ดูว่ามี cursor ค้างที่ไม่มีใครอ่านต่ออยู่เท่าไร",
            "ปรับโครงสร้างโค้ด: ดึงข้อมูลเป็นชุดให้จบก่อนแล้วค่อยประมวลผล อย่าประมวลผลหนักคาไว้ในลูปที่วน cursor",
            "ทางที่ปลอดภัยกว่าคือแบ่งอ่านเป็นหน้าด้วย range query บน _id: db.<coll>.find({_id: {$gt: <last_id>}}).sort({_id: 1}).limit(1000) แล้ววนต่อจาก _id ตัวสุดท้าย — ไม่ต้องถือ cursor ข้ามรอบเลย",
            "ถ้าเป็นงาน batch ที่ยาวจริงและเลี่ยงไม่ได้: db.<coll>.find(<filter>).noCursorTimeout() แล้วต้องเรียก cursor.close() เองเสมอ ไม่งั้น cursor จะค้างถาวรกินทรัพยากร",
            "ปรับเพดานทั้งระบบเป็นทางเลือกสุดท้าย: db.adminCommand({setParameter: 1, cursorTimeoutMillis: 1800000})",
        ],
        "docs_url": "https://www.mongodb.com/docs/manual/reference/method/cursor.noCursorTimeout/",
        "applies_to": ">=4.4",
    },
    {
        "id": "mongodb.write_concern_timeout",
        "engine": "mongodb",
        "frame": "Database",
        "severity": "high",
        "title": "Write concern timeout",
        "symptom_text": (
            "mongodb WriteConcernFailed waiting for replication timed out wtimeout "
            "w majority write concern could not be satisfied "
            "write ไม่ผ่านเพราะรอ replica ยืนยันไม่ทัน"
        ),
        "error_codes": ["64", "100"],
        "root_cause_chain": [
            "write ต้องรอให้สมาชิกครบตามที่ write concern กำหนด (เช่น majority) ยืนยันก่อนถือว่าสำเร็จ",
            "ถ้ารอเกิน wtimeout จะคืน error ทั้งที่ข้อมูลอาจถูกเขียนลง primary ไปแล้ว — จุดนี้ทำให้เข้าใจผิดได้ง่าย",
            "ต้นตอคือ secondary ตามไม่ทันหรือหายไป ไม่ใช่ตัว write เอง",
        ],
        "verify_steps": [
            "rs.status() — ดูว่าสมาชิกครบและ health: 1 หรือไม่",
            "rs.printSecondaryReplicationInfo() — ดูว่า secondary ตามหลังเท่าไร",
        ],
        "fix_steps": [
            "rs.status() — ตรวจว่าจำนวนสมาชิกที่ยังดีอยู่พอที่จะให้ majority ยืนยันได้ไหม",
            "ถ้ามี secondary ตามไม่ทัน ให้แก้ที่สาเหตุความช้าก่อน (I/O, index build, เครือข่าย)",
            "อย่าลด write concern ลงจาก majority เพื่อเลี่ยงปัญหา — จะเปิดโอกาสให้ข้อมูลหายตอน failover",
            "ปรับ wtimeout ให้สมเหตุสมผลกับ replication lag ปกติของระบบ แล้วให้ application retry",
            "สำคัญ: เมื่อเจอ error นี้ อย่าเขียนซ้ำโดยไม่ตรวจก่อน เพราะ write เดิมอาจสำเร็จไปแล้ว — ใช้ operation ที่ idempotent",
        ],
        "docs_url": "https://www.mongodb.com/docs/manual/reference/write-concern/",
        "applies_to": ">=4.4",
    },
    {
        "id": "mongodb.chunk_migration_stuck",
        "engine": "mongodb",
        "frame": "Database",
        "severity": "medium",
        "title": "Chunk migration ค้าง (sharded cluster)",
        "symptom_text": (
            "mongodb chunk migration failed balancer stuck moveChunk "
            "sharded cluster jumbo chunk unbalanced shard distribution "
            "balancer ย้าย chunk ไม่สำเร็จ ข้อมูลกระจายไม่สมดุล"
        ),
        "error_codes": ["96"],
        "root_cause_chain": [
            "balancer ย้าย chunk ระหว่าง shard ไม่สำเร็จ ทำให้ข้อมูลกองอยู่ที่ shard เดียว",
            "shard ที่รับภาระเกินจะช้าและเต็มก่อนตัวอื่น ทั้งที่คลัสเตอร์ยังมีที่ว่างรวมกันเหลือ",
            "สาเหตุที่พบบ่อยคือ jumbo chunk (chunk ที่ใหญ่เกินย้ายไม่ได้) ซึ่งเกิดจาก shard key ที่กระจายตัวไม่ดีพอ",
        ],
        "verify_steps": [
            "sh.status() — ดูการกระจายของ chunk ต่อ shard ว่าเบ้ไปทางไหน และมี jumbo ไหม",
            "db.getSiblingDB('config').changelog.find({what: /moveChunk/}).sort({time:-1}).limit(10) — ดูว่าการย้ายล่าสุดล้มเหลวด้วยสาเหตุอะไร",
        ],
        "fix_steps": [
            "sh.status() — ดูจำนวน chunk ต่อ shard และมองหา jumbo chunk",
            "ตรวจว่า balancer เปิดอยู่: sh.getBalancerState() และไม่ได้ติด balancing window ที่ตั้งไว้",
            "ดูสาเหตุจริงใน config.changelog ก่อนลงมือ — 'ย้ายไม่ได้' กับ 'ไม่ได้พยายามย้าย' แก้คนละแบบ",
            "ถ้าเป็น jumbo chunk: แก้ที่ shard key เป็นหลัก การ split ด้วยมือเป็นการซื้อเวลาเท่านั้น",
            "shard key ที่กระจายตัวไม่ดี (เช่นค่าที่เพิ่มขึ้นเรื่อยๆ อย่าง timestamp) ทำให้ write ไปกองที่ shard เดียว — พิจารณา hashed shard key หรือ compound key",
        ],
        "docs_url": "https://www.mongodb.com/docs/manual/core/sharding-balancer-administration/",
        "applies_to": ">=4.4",
    },
]
