"""PostgreSQL playbooks — cold-start knowledge for A4.

Written from operational experience rather than copied from the manual; see
`docs_url` for the authoritative reference. See mysql.py for why symptom_text
mixes log tokens with plain description.
"""
from __future__ import annotations

PLAYBOOK_ENTRIES: list[dict] = [
    {
        "id": "postgresql.deadlock_detected",
        "engine": "postgresql",
        "frame": "Database",
        "severity": "high",
        "title": "Deadlock detected",
        "symptom_text": (
            "postgresql ERROR deadlock detected 40P01 Process waits for ShareLock on transaction "
            "deadlock_timeout circular wait canceled transaction "
            "สอง transaction ล็อกไขว้กัน postgres ยกเลิกตัวหนึ่ง"
        ),
        "error_codes": ["40P01"],
        "root_cause_chain": [
            "transaction ตั้งแต่สองตัวขึ้นไปรอ lock ของกันและกันเป็นวงจรปิด",
            "PostgreSQL ตรวจเจอหลังพ้น deadlock_timeout แล้วยกเลิก transaction หนึ่งเพื่อปลดวงจร",
            "สาเหตุที่พบบ่อยคือโค้ดสองเส้นทางเข้าถึงแถวเดียวกันคนละลำดับ",
        ],
        "verify_steps": [
            "ดู log บรรทัด DETAIL ใต้ error — จะบอกว่า process ไหนรออะไรอยู่ ครบทั้งวงจร",
            "SELECT * FROM pg_stat_activity WHERE wait_event_type = 'Lock' — ดูว่ายังมีคนรอค้างอยู่ไหม",
        ],
        "fix_steps": [
            "อ่านบรรทัด DETAIL และ STATEMENT ใน log ให้ครบทั้งสองฝั่ง — PostgreSQL บอกมาตรงๆ ว่าใครรออะไร",
            "ไล่โค้ดว่าทั้งสองเส้นทางแตะตารางตามลำดับต่างกันหรือไม่ แล้วบังคับให้เรียงเหมือนกัน",
            "ถ้าเป็นการอัปเดตหลายแถวในคำสั่งเดียว: เติม ORDER BY ใน SELECT ... FOR UPDATE เพื่อล็อกตามลำดับที่แน่นอน",
            "ให้ application retry เมื่อเจอ SQLSTATE 40P01 — เป็นภาวะปกติของระบบ concurrent",
            "ลดขนาด transaction ให้สั้นลง ยิ่งถือ lock สั้น โอกาสไขว้ยิ่งน้อย",
        ],
        "docs_url": "https://www.postgresql.org/docs/current/explicit-locking.html#LOCKING-DEADLOCKS",
        "applies_to": ">=12",
    },
    {
        "id": "postgresql.too_many_clients",
        "engine": "postgresql",
        "frame": "Database",
        "severity": "critical",
        "title": "Too many clients already",
        "symptom_text": (
            "postgresql FATAL sorry too many clients already 53300 max_connections "
            "connection pool exhausted remaining connection slots reserved for superuser "
            "ต่อ database ไม่ได้เพราะ connection เต็ม"
        ),
        "error_codes": ["53300", "53400"],
        "root_cause_chain": [
            "จำนวน backend process ที่เปิดอยู่ชนเพดาน max_connections",
            "connection ใหม่ถูกปฏิเสธทันที service ที่พึ่ง DB จึงล้มตาม",
            "PostgreSQL ใช้ process ต่อหนึ่ง connection ซึ่งกินทรัพยากรมากกว่า thread เพดานจึงตั้งสูงไม่ได้เท่า engine อื่น — ต้องมี connection pooler มาคั่น",
        ],
        "verify_steps": [
            "SELECT count(*) FROM pg_stat_activity — เทียบกับ SHOW max_connections",
            "SELECT state, count(*) FROM pg_stat_activity GROUP BY state — ถ้า 'idle in transaction' เยอะ ปัญหาอยู่ที่ application ไม่ commit ไม่ใช่เพดานต่ำ",
        ],
        "fix_steps": [
            "SELECT state, count(*) FROM pg_stat_activity GROUP BY state — แยกให้ออกก่อนว่าเป็น idle, idle in transaction, หรือ active",
            "ถ้า 'idle in transaction' เยอะ: ตั้ง idle_in_transaction_session_timeout = '60s' เพื่อกันไม่ให้ connection ค้างถาวร",
            "เร่งด่วน: SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE state = 'idle in transaction' AND state_change < now() - interval '10 minutes'",
            "ทางแก้จริงคือใส่ connection pooler (PgBouncer) แบบ transaction mode — การเพิ่ม max_connections มักทำให้แย่ลงเพราะ process เยอะขึ้นแย่ง CPU/RAM",
            "ตรวจ pool size ฝั่ง application: ผลรวมทุก instance ต้องน้อยกว่า max_connections โดยเหลือ slot สำรองไว้ให้ admin",
        ],
        "docs_url": "https://www.postgresql.org/docs/current/runtime-config-connection.html",
        "applies_to": ">=12",
    },
    {
        "id": "postgresql.disk_full_wal",
        "engine": "postgresql",
        "frame": "Hardware",
        "severity": "critical",
        "title": "ดิสก์เต็มจาก WAL",
        "symptom_text": (
            "postgresql PANIC could not write to file pg_wal No space left on device 53100 "
            "disk full WAL accumulating checkpoint failed database shutdown "
            "ดิสก์เต็มเพราะ WAL สะสม database หยุดทำงาน"
        ),
        "error_codes": ["53100"],
        "root_cause_chain": [
            "พาร์ทิชันที่เก็บ pg_wal เต็ม PostgreSQL จึงหยุดทันทีเพื่อกันข้อมูลเสียหาย",
            "WAL สะสมได้ทั้งจาก replication slot ที่ไม่มีใครอ่าน, archive_command ที่ล้มเหลวเงียบๆ, หรือ checkpoint ที่ห่างเกินไป",
            "replication slot ที่ถูกทิ้งร้างเป็นสาเหตุที่เจอบ่อยที่สุด เพราะ PostgreSQL จะเก็บ WAL ไว้ตลอดกาลจนกว่า slot จะมาอ่าน",
        ],
        "verify_steps": [
            "df -h $(psql -tAc 'SHOW data_directory')/pg_wal — ยืนยันว่าเต็มจริง",
            "SELECT slot_name, active, pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)) AS retained FROM pg_replication_slots — slot ที่ active=false และ retained ใหญ่คือตัวปัญหา",
        ],
        "fix_steps": [
            "SELECT * FROM pg_replication_slots WHERE NOT active — หา slot ที่ไม่มีใครใช้แล้ว",
            "ถ้าแน่ใจว่า replica ตัวนั้นเลิกใช้แล้ว: SELECT pg_drop_replication_slot('<slot_name>') — WAL จะถูกปล่อยทันที",
            "ตรวจ archive_command: SELECT * FROM pg_stat_archiver — ถ้า failed_count เพิ่มเรื่อยๆ แปลว่า archive ล้มเหลวและ WAL ค้างเพราะรอ archive สำเร็จ",
            "ตั้ง max_slot_wal_keep_size เพื่อกันไม่ให้ slot เดียวถ่วงจนดิสก์เต็มได้อีก",
            "อย่าลบไฟล์ใน pg_wal ด้วย rm เด็ดขาด — จะทำให้ database กู้ไม่ได้ ใช้ pg_archivecleanup ถ้าจำเป็นจริงๆ",
        ],
        "docs_url": "https://www.postgresql.org/docs/current/wal-configuration.html",
        "applies_to": ">=12",
    },
    {
        "id": "postgresql.transaction_id_wraparound",
        "engine": "postgresql",
        "frame": "Database",
        "severity": "critical",
        "title": "Transaction ID wraparound / autovacuum ตามไม่ทัน",
        "symptom_text": (
            "postgresql WARNING database must be vacuumed within transactions "
            "autovacuum not keeping up transaction id wraparound age datfrozenxid "
            "table bloat dead tuples vacuum freeze "
            "autovacuum ตามไม่ทัน เสี่ยง database หยุดรับ write"
        ),
        "error_codes": ["54000"],
        "root_cause_chain": [
            "PostgreSQL ใช้ transaction id แบบวนรอบ ต้อง freeze แถวเก่าก่อนจะวนมาชนกัน",
            "ถ้า autovacuum ตามไม่ทัน อายุ (age) จะเพิ่มเรื่อยๆ และเมื่อถึงเพดาน database จะหยุดรับ write เพื่อกันข้อมูลเสียหาย",
            "สาเหตุที่พบบ่อย: transaction ที่เปิดค้างนานมาก, replication slot ที่ไม่มีใครอ่าน, หรือ prepared transaction ที่ค้าง — ทั้งสามอย่างกัน vacuum ไม่ให้ freeze แถวได้",
        ],
        "verify_steps": [
            "SELECT datname, age(datfrozenxid) FROM pg_database ORDER BY 2 DESC — ถ้าเข้าใกล้ 200 ล้านคือเริ่มอันตราย",
            "SELECT max(age(backend_xmin)) FROM pg_stat_activity — บอกว่ามี transaction เก่าค้างขวางอยู่ไหม",
        ],
        "fix_steps": [
            "SELECT relname, age(relfrozenxid) FROM pg_class WHERE relkind='r' ORDER BY 2 DESC LIMIT 20 — หาตารางที่แก่ที่สุด",
            "หาตัวขวาง: SELECT pid, state, xact_start, backend_xmin FROM pg_stat_activity ORDER BY backend_xmin — transaction ที่เปิดค้างต้องถูกปิดก่อน vacuum ถึงจะคืบ",
            "ตรวจ prepared transaction ที่ค้าง: SELECT * FROM pg_prepared_xacts — ถ้ามีของเก่าให้ ROLLBACK PREPARED",
            "ตรวจ replication slot ที่ไม่ active ด้วย — มันกัน vacuum เหมือนกัน",
            "เร่ง vacuum ตารางที่แก่สุด: VACUUM (FREEZE, VERBOSE) <table>",
            "ปรับ autovacuum ให้ก้าวร้าวขึ้นบนตารางที่เขียนหนัก: ALTER TABLE <t> SET (autovacuum_vacuum_scale_factor = 0.02)",
        ],
        "docs_url": "https://www.postgresql.org/docs/current/routine-vacuuming.html",
        "applies_to": ">=12",
    },
    {
        "id": "postgresql.idle_in_transaction",
        "engine": "postgresql",
        "frame": "Database",
        "severity": "high",
        "title": "Idle in transaction ค้างจนบล็อกตัวอื่น",
        "symptom_text": (
            "postgresql idle in transaction blocking lock not granted waiting "
            "pg_stat_activity state idle in transaction long running open transaction "
            "connection เปิด transaction ค้างไว้ ไม่ commit ทำให้ตัวอื่นรอ"
        ),
        "error_codes": ["55P03"],
        "root_cause_chain": [
            "application เปิด transaction แล้วไปทำอย่างอื่นต่อโดยไม่ commit/rollback",
            "transaction นั้นยังถือ lock อยู่ ทำให้คำสั่งอื่นที่ต้องการแถวเดียวกันรอค้าง",
            "ผลข้างเคียงที่มองไม่เห็นคือมันกัน vacuum ไม่ให้เก็บ dead tuple ทำให้ตารางบวมไปพร้อมกัน",
        ],
        "verify_steps": [
            "SELECT pid, state, now()-state_change AS idle_for, query FROM pg_stat_activity WHERE state='idle in transaction' ORDER BY 3 DESC",
            "SELECT * FROM pg_locks WHERE NOT granted — ดูว่ามีใครรออยู่จริงไหม",
        ],
        "fix_steps": [
            "หาตัวค้าง: SELECT pid, now()-state_change AS idle_for, query FROM pg_stat_activity WHERE state='idle in transaction' ORDER BY 2 DESC",
            "เร่งด่วน: SELECT pg_terminate_backend(<pid>) กับตัวที่ค้างนานผิดปกติ",
            "ตั้งกันซ้ำ: ALTER SYSTEM SET idle_in_transaction_session_timeout = '60s'; SELECT pg_reload_conf()",
            "ฝั่ง application: ตรวจว่าไม่มีการเรียก API ภายนอกหรือรอ I/O ระหว่างที่ transaction เปิดอยู่ — เป็นต้นเหตุอันดับหนึ่ง",
            "ตรวจว่า ORM ไม่ได้เปิด transaction ให้อัตโนมัติตั้งแต่ต้น request แล้วปิดตอนจบ request",
        ],
        "docs_url": "https://www.postgresql.org/docs/current/monitoring-stats.html",
        "applies_to": ">=12",
    },
    {
        "id": "postgresql.replication_lag",
        "engine": "postgresql",
        "frame": "Database",
        "severity": "high",
        "title": "Replica ตามไม่ทัน",
        "symptom_text": (
            "postgresql replication lag standby behind primary replay_lag write_lag "
            "pg_stat_replication streaming replication delay stale read "
            "replica ตามหลัง อ่านได้ข้อมูลเก่า"
        ),
        "error_codes": [],
        "root_cause_chain": [
            "standby รับหรือ replay WAL ช้ากว่าที่ primary ผลิต ระยะห่างจึงถ่างขึ้น",
            "read query ที่ถูกส่งไป standby ได้ข้อมูลเก่ากว่าความจริง",
            "สาเหตุที่พบบ่อย: I/O ของ standby ช้ากว่า primary, query บน standby ที่ยาวจนขวาง replay (เมื่อ hot_standby_feedback เปิด), หรือแบนด์วิดท์ระหว่างสองเครื่องไม่พอ",
        ],
        "verify_steps": [
            "บน primary: SELECT client_addr, state, write_lag, flush_lag, replay_lag FROM pg_stat_replication",
            "บน standby: SELECT now() - pg_last_xact_replay_timestamp() AS lag — ค่าที่โตขึ้นเรื่อยๆ คือตามไม่ทันจริง",
        ],
        "fix_steps": [
            "SELECT * FROM pg_stat_replication บน primary — ดูว่าช้าที่ขั้น write, flush หรือ replay ทั้งสามชี้คนละสาเหตุ",
            "ถ้าช้าที่ replay: หา query ยาวบน standby ที่ขวางอยู่ SELECT pid, now()-query_start, query FROM pg_stat_activity ORDER BY 2 DESC",
            "ถ้าช้าที่ write/flush: ปัญหาอยู่ที่เครือข่ายหรือ I/O ของ standby ไม่ใช่ที่ query",
            "พิจารณา max_standby_streaming_delay — ค่าสูงทำให้ query บน standby รอด แต่ lag ยาว ค่าต่ำทำให้ query ถูกยกเลิกแต่ replay ทัน ต้องเลือกตามการใช้งาน",
            "ระหว่าง lag สูง ให้ย้าย read ที่ต้องการข้อมูลสดกลับไป primary ชั่วคราว",
        ],
        "docs_url": "https://www.postgresql.org/docs/current/warm-standby.html",
        "applies_to": ">=12",
    },
    {
        "id": "postgresql.statement_timeout_canceled",
        "engine": "postgresql",
        "frame": "Database",
        "severity": "medium",
        "title": "Query ถูกยกเลิกเพราะหมดเวลา",
        "symptom_text": (
            "postgresql ERROR canceling statement due to statement timeout 57014 "
            "query cancelled lock timeout user request slow query "
            "query ถูกยกเลิกเพราะเกินเวลาที่กำหนด"
        ),
        "error_codes": ["57014"],
        "root_cause_chain": [
            "query ใช้เวลาเกิน statement_timeout จึงถูกยกเลิกกลางคัน",
            "อาการนี้เป็นสัญญาณ ไม่ใช่โรค — ต้องแยกว่า query ช้าเองหรือถูกคนอื่นบล็อกอยู่",
            "ถ้าถูกบล็อก ต้นตอคือ lock ของ transaction อื่น ไม่ใช่ตัว query นี้",
        ],
        "verify_steps": [
            "SELECT * FROM pg_locks WHERE NOT granted — ถ้ามีแปลว่ารอ lock ไม่ใช่ query ช้าเอง",
            "EXPLAIN (ANALYZE, BUFFERS) <query> ตอนระบบว่าง — ถ้าเร็วปกติแปลว่าปัญหาคือการแย่ง lock",
        ],
        "fix_steps": [
            "แยกให้ออกก่อน: SELECT blocked.pid, blocking.pid AS blocked_by FROM pg_stat_activity blocked JOIN pg_stat_activity blocking ON blocking.pid = ANY(pg_blocking_pids(blocked.pid))",
            "ถ้าถูกบล็อก: ไปแก้ที่ transaction ที่ถือ lock (ดู postgresql.idle_in_transaction) การเพิ่ม timeout ไม่ช่วย",
            "ถ้า query ช้าเอง: EXPLAIN (ANALYZE, BUFFERS) แล้วดูว่าเป็น Seq Scan บนตารางใหญ่หรือไม่",
            "เพิ่ม index ให้ตรงกับ WHERE + ORDER BY แล้ว EXPLAIN ซ้ำเพื่อยืนยันว่าถูกใช้จริง",
            "ปรับ statement_timeout เฉพาะงานที่รู้ว่ายาว (เช่น report) ด้วย SET LOCAL แทนการปรับทั้งระบบ",
        ],
        "docs_url": "https://www.postgresql.org/docs/current/runtime-config-client.html",
        "applies_to": ">=12",
    },
    {
        "id": "postgresql.connection_refused",
        "engine": "postgresql",
        "frame": "Network",
        "severity": "critical",
        "title": "ต่อ PostgreSQL ไม่ได้",
        "symptom_text": (
            "postgresql could not connect to server connection refused 08006 08001 "
            "no pg_hba.conf entry for host FATAL password authentication failed "
            "server closed the connection unexpectedly ต่อ database ไม่ได้"
        ),
        "error_codes": ["08006", "08001", "28000", "28P01"],
        "root_cause_chain": [
            "client ต่อไม่ติดหรือถูกตัดทันทีที่เชื่อมต่อ",
            "SQLSTATE บอกทางแยกได้: 08xxx คือระดับเครือข่าย/เซิร์ฟเวอร์, 28xxx คือถูกปฏิเสธเพราะสิทธิ์",
            "สาเหตุที่พบบ่อยเรียงตามความถี่: pg_hba.conf ไม่มี rule ให้ host นั้น, listen_addresses ไม่ได้เปิดรับจากภายนอก, service ไม่ได้รัน, firewall กั้น",
        ],
        "verify_steps": [
            "pg_isready -h <host> -p 5432 — แยกให้ออกว่าเป็นปัญหาเครือข่ายหรือปัญหาสิทธิ์",
            "ดู server log — ถ้าเห็น 'no pg_hba.conf entry' แปลว่าต่อถึงเซิร์ฟเวอร์แล้วแต่ถูกปฏิเสธ ไม่ใช่ปัญหาเครือข่าย",
        ],
        "fix_steps": [
            "pg_isready -h <host> -p 5432 — ถ้าไม่ตอบเลยให้ไปดู service กับ firewall ก่อน",
            "ตรวจว่า service รันอยู่: systemctl status postgresql",
            "SHOW listen_addresses — ถ้าเป็น 'localhost' จะไม่รับ connection จากเครื่องอื่น",
            "ตรวจ pg_hba.conf ว่ามีบรรทัดครอบคลุม (user, database, source CIDR, auth method) ที่ตรงกับ client จริง — ลำดับบรรทัดมีผล ตัวแรกที่ match ถูกใช้",
            "หลังแก้ pg_hba.conf: SELECT pg_reload_conf() — ไม่ต้อง restart",
            "ถ้าเป็น 28P01 (password ผิด) ให้ไปตรวจ credential ที่ application ใช้ ไม่ใช่ตั้งค่าเซิร์ฟเวอร์",
        ],
        "docs_url": "https://www.postgresql.org/docs/current/auth-pg-hba-conf.html",
        "applies_to": ">=12",
    },
    {
        "id": "postgresql.table_bloat",
        "engine": "postgresql",
        "frame": "Database",
        "severity": "medium",
        "title": "ตารางบวมจาก dead tuple",
        "symptom_text": (
            "postgresql table bloat dead tuples n_dead_tup autovacuum "
            "index bloat sequential scan slower table size growing disk usage "
            "ตารางบวม query ช้าลงทั้งที่ข้อมูลเท่าเดิม"
        ),
        "error_codes": [],
        "root_cause_chain": [
            "UPDATE และ DELETE ใน PostgreSQL ไม่ได้ลบแถวทิ้งทันที แต่ทิ้งเป็น dead tuple ให้ vacuum มาเก็บทีหลัง",
            "ถ้า vacuum ตามไม่ทัน ตารางและ index จะโตขึ้นเรื่อยๆ ทั้งที่จำนวนแถวจริงเท่าเดิม",
            "ผลคือ query ต้องอ่าน page มากขึ้นเพื่อได้ข้อมูลเท่าเดิม ทุกอย่างช้าลงแบบค่อยเป็นค่อยไปจนไม่ทันสังเกต",
        ],
        "verify_steps": [
            "SELECT relname, n_live_tup, n_dead_tup, last_autovacuum FROM pg_stat_user_tables ORDER BY n_dead_tup DESC LIMIT 20",
            "ถ้า n_dead_tup เกิน 20% ของ n_live_tup และ last_autovacuum เก่ามาก คือบวมจริง",
        ],
        "fix_steps": [
            "SELECT relname, n_dead_tup, last_autovacuum FROM pg_stat_user_tables ORDER BY n_dead_tup DESC LIMIT 20 — หาตารางที่แย่สุด",
            "ตรวจก่อนว่าอะไรกัน vacuum อยู่: transaction เก่าค้าง, replication slot ที่ไม่ active, prepared transaction — ถ้าไม่แก้ตรงนี้ vacuum ก็ทำงานไม่ได้อยู่ดี",
            "เก็บกวาดแบบไม่ล็อก: VACUUM (VERBOSE, ANALYZE) <table>",
            "ถ้าบวมมากจนต้องคืนที่จริงๆ: ใช้ pg_repack (ไม่ล็อกตาราง) แทน VACUUM FULL ซึ่งล็อกทั้งตารางและใช้ไม่ได้บนระบบ production",
            "ปรับ autovacuum ให้ถี่ขึ้นเฉพาะตารางที่เขียนหนัก: ALTER TABLE <t> SET (autovacuum_vacuum_scale_factor = 0.02)",
        ],
        "docs_url": "https://www.postgresql.org/docs/current/routine-vacuuming.html",
        "applies_to": ">=12",
    },
    {
        "id": "postgresql.oom_killed",
        "engine": "postgresql",
        "frame": "Hardware",
        "severity": "critical",
        "title": "postgres ถูก OOM killer ฆ่า",
        "symptom_text": (
            "postgresql out of memory killed process postmaster oom-killer "
            "server process was terminated by signal 9 work_mem shared_buffers "
            "terminating connection because of crash of another server process "
            "หน่วยความจำไม่พอ process ถูกฆ่าแล้ว database restart"
        ),
        "error_codes": ["53200"],
        "root_cause_chain": [
            "kernel ต้องการ memory คืนแล้วเลือกฆ่า backend process ของ PostgreSQL",
            "เมื่อ backend ตัวใดตัวหนึ่งตายผิดปกติ postmaster จะรีเซ็ตทุก connection เพื่อความปลอดภัยของ shared memory",
            "ต้นตอมักเป็น work_mem ที่ตั้งไว้สูงคูณกับจำนวน connection และจำนวน sort/hash node ต่อ query — ค่านี้เป็นต่อ operation ไม่ใช่ต่อ connection",
        ],
        "verify_steps": [
            "dmesg -T | grep -i 'killed process' — ต้องเห็น postgres พร้อมเวลาที่ตรงกัน",
            "ดู log ว่ามี 'terminating connection because of crash of another server process' ไหม — ยืนยันว่าเป็นการตายผิดปกติ",
        ],
        "fix_steps": [
            "dmesg -T | grep -i oom — ยืนยันเวลาและ process",
            "ทบทวน work_mem: ค่านี้ถูกใช้ต่อ sort/hash หนึ่งครั้ง query เดียวใช้ได้หลายก้อน ค่าที่ดูเล็กจึงบานปลายได้ง่าย",
            "ตั้ง work_mem ให้ต่ำเป็นค่าพื้นฐาน แล้วใช้ SET LOCAL work_mem เฉพาะ query หนักที่รู้จัก",
            "shared_buffers ราว 25% ของ RAM เป็นจุดตั้งต้นที่ปลอดภัยสำหรับเครื่องที่รัน PostgreSQL อย่างเดียว",
            "ตั้ง vm.overcommit_memory = 2 เพื่อให้การจองหน่วยความจำล้มเหลวตรงๆ แทนที่จะโดน OOM killer ฆ่าทีหลัง",
            "ถ้ามี connection เยอะ ให้ใส่ PgBouncer ลดจำนวน backend จริงลง",
        ],
        "docs_url": "https://www.postgresql.org/docs/current/kernel-resources.html",
        "applies_to": ">=12",
    },
]
