"""MySQL / MariaDB playbooks — cold-start knowledge for A4.

Descriptions are written from operational experience, not copied from vendor
documentation; `docs_url` points at the authoritative source instead.

`symptom_text` is what gets embedded and compared against the symptom_text A4
builds from real logs, so it deliberately mixes two registers: tokens as they
appear in the log ("Lock wait timeout exceeded") and how an operator would
describe the situation ("transaction ค้างถือ lock"). Matching only one of those
loses half the recall.
"""
from __future__ import annotations

PLAYBOOK_ENTRIES: list[dict] = [
    {
        "id": "mysql.innodb_lock_wait_timeout",
        "engine": "mysql",
        "frame": "Database",
        "severity": "high",
        "title": "InnoDB lock wait timeout",
        "symptom_text": (
            "mysql innodb lock wait timeout exceeded try restarting transaction "
            "ERROR 1205 HY000 long running transaction blocking row lock "
            "innodb_lock_wait_timeout trx_rows_locked "
            "transaction ค้างถือ row lock นานเกินกำหนด ตัวอื่นรอจนหมดเวลา"
        ),
        "error_codes": ["1205", "HY000"],
        "root_cause_chain": [
            "มี transaction ค้างถือ row lock นานเกิน innodb_lock_wait_timeout (default 50 วินาที)",
            "transaction อื่นที่ต้องการ lock แถวเดียวกันรอจนหมดเวลาแล้วถูก rollback",
            "ต้นตอที่พบบ่อยคือ transaction ที่เปิดทิ้งไว้ไม่ commit หรือ query ที่ scan แถวมากเกินจำเป็นจน lock กว้างกว่าที่ควร",
        ],
        "verify_steps": [
            "SELECT trx_id, trx_started, trx_state, trx_rows_locked, trx_query FROM information_schema.innodb_trx ORDER BY trx_started — ถ้ามี trx ที่ trx_started เก่ากว่า 60 วินาที คือตัวต้นเหตุ",
            "SHOW GLOBAL STATUS LIKE 'Innodb_row_lock_waits' — ค่าต้องเพิ่มต่อเนื่อง ไม่ใช่นิ่ง",
        ],
        "fix_steps": [
            "SELECT * FROM information_schema.innodb_trx ORDER BY trx_started — หา transaction ที่ค้างนานที่สุด",
            "SHOW ENGINE INNODB STATUS — อ่านส่วน TRANSACTIONS ดูว่าใครถือ lock อยู่",
            "EXPLAIN <query ที่ค้าง> — ถ้าเป็น full scan แปลว่า lock กว้างเกินเพราะไม่มี index รองรับ",
            "เร่งด่วน: KILL <trx_mysql_thread_id> ของ transaction ที่ค้าง",
            "ระยะยาว: เพิ่ม index ที่ query ใช้, ลดขนาด transaction, ตรวจว่า application ไม่เปิด transaction ทิ้งไว้ระหว่างรอ I/O ภายนอก",
        ],
        "docs_url": "https://dev.mysql.com/doc/refman/8.0/en/innodb-parameters.html#sysvar_innodb_lock_wait_timeout",
        "applies_to": ">=5.7",
    },
    {
        "id": "mysql.deadlock_found",
        "engine": "mysql",
        "frame": "Database",
        "severity": "high",
        "title": "InnoDB deadlock detected",
        "symptom_text": (
            "mysql innodb deadlock found when trying to get lock try restarting transaction "
            "ERROR 1213 40001 LATEST DETECTED DEADLOCK circular wait "
            "สอง transaction ล็อกไขว้กัน innodb เลือก rollback ตัวหนึ่งทิ้ง"
        ),
        "error_codes": ["1213", "40001"],
        "root_cause_chain": [
            "สอง transaction ขึ้นไปถือ lock แล้วต่างฝ่ายต่างรอ lock ของอีกฝ่าย เกิดวงจรรอแบบปิด",
            "InnoDB ตรวจเจอแล้วเลือก rollback transaction ที่แก้แถวน้อยกว่าเพื่อปลดวงจร",
            "มักเกิดจาก transaction สองชุดเข้าถึงแถวเดียวกันคนละลำดับ หรือ secondary index ทำให้ลำดับการ lock ไม่ตรงกับที่โค้ดคิด",
        ],
        "verify_steps": [
            "SHOW ENGINE INNODB STATUS — ส่วน LATEST DETECTED DEADLOCK ต้องมีเวลาใกล้กับที่ error เกิด",
            "SHOW GLOBAL STATUS LIKE 'Innodb_deadlocks' — ดูว่าเป็นเหตุการณ์เดี่ยวหรือเกิดซ้ำ",
        ],
        "fix_steps": [
            "SHOW ENGINE INNODB STATUS — อ่าน LATEST DETECTED DEADLOCK ให้ครบทั้งสองฝั่ง ดูว่าแต่ละ transaction ถืออะไรและรออะไร",
            "ไล่ดูว่าทั้งสอง transaction แตะตารางเดียวกันตามลำดับต่างกันหรือไม่ — ถ้าใช่ ให้บังคับลำดับการเข้าถึงให้เหมือนกันทุกที่ในโค้ด",
            "EXPLAIN ทั้งสอง query — index ที่ขาดทำให้ lock กินแถวเกินจำเป็นและเพิ่มโอกาสไขว้",
            "ให้ application retry transaction ที่โดน 1213 ได้เอง — deadlock เป็นเรื่องปกติของระบบที่ concurrent สูง ไม่ใช่ความผิดพลาดร้ายแรง",
            "ถ้ายังถี่: ลดขนาด transaction ให้สั้นลง และหลีกเลี่ยงการอัปเดตหลายแถวในคำสั่งเดียวโดยไม่เรียงลำดับ",
        ],
        "docs_url": "https://dev.mysql.com/doc/refman/8.0/en/innodb-deadlocks.html",
        "applies_to": ">=5.7",
    },
    {
        "id": "mysql.too_many_connections",
        "engine": "mysql",
        "frame": "Database",
        "severity": "critical",
        "title": "Too many connections",
        "symptom_text": (
            "mysql ERROR 1040 HY000 Too many connections max_connections reached "
            "connection pool exhausted client cannot connect to database "
            "เชื่อมต่อไม่ได้เพราะ connection เต็มเพดาน"
        ),
        "error_codes": ["1040", "HY000"],
        "root_cause_chain": [
            "จำนวน connection ที่เปิดพร้อมกันชนเพดาน max_connections",
            "client ใหม่ถูกปฏิเสธทันที ทำให้ service ที่ต่อ DB ล้มเป็นลูกโซ่",
            "ต้นตอมักเป็นสองแบบ: connection pool ฝั่ง application ตั้งใหญ่เกินรวมกันหลาย instance หรือมี query ช้าที่ทำให้ connection ไม่ถูกคืน",
        ],
        "verify_steps": [
            "SHOW GLOBAL STATUS LIKE 'Threads_connected' เทียบกับ SHOW VARIABLES LIKE 'max_connections' — ถ้าชิดกันคือชนเพดานจริง",
            "SHOW GLOBAL STATUS LIKE 'Max_used_connections' — บอกว่าเคยขึ้นไปสูงสุดเท่าไร",
        ],
        "fix_steps": [
            "SHOW PROCESSLIST — ดูว่า connection ส่วนใหญ่อยู่สถานะอะไร ถ้าเป็น Sleep เยอะแปลว่า pool ไม่คืน connection ถ้าเป็น Query ค้างแปลว่ามี query ช้า",
            "เร่งด่วน: SET GLOBAL max_connections = <ค่าใหม่> — มีผลทันทีไม่ต้อง restart แต่เป็นการซื้อเวลา ไม่ใช่การแก้",
            "ตรวจ pool size ฝั่ง application: ผลรวมของทุก instance ต้องน้อยกว่า max_connections โดยเหลือ slot ให้ admin",
            "หา query ที่ค้าง: SELECT * FROM information_schema.processlist WHERE command='Query' AND time > 10",
            "ตรวจว่า max_connections ไม่เกินที่ RAM รับไหว — แต่ละ connection กินหน่วยความจำตาม buffer ต่อ session",
        ],
        "docs_url": "https://dev.mysql.com/doc/refman/8.0/en/too-many-connections.html",
        "applies_to": ">=5.7",
    },
    {
        "id": "mysql.disk_full",
        "engine": "mysql",
        "frame": "Hardware",
        "severity": "critical",
        "title": "ดิสก์เต็ม — InnoDB เขียนไฟล์ไม่ได้",
        "symptom_text": (
            "mysql InnoDB Write to file failed OS error 28 No space left on device "
            "ibdata1 ib_logfile disk full table is full ERROR 1021 "
            "ดิสก์เต็ม เขียน tablespace ไม่ได้ database หยุดรับ write"
        ),
        "error_codes": ["28", "1021"],
        "root_cause_chain": [
            "พาร์ทิชันที่เก็บ datadir หรือ binlog เต็ม",
            "InnoDB เขียน tablespace / redo log ไม่สำเร็จ จึงหยุดรับ write เพื่อกันข้อมูลเสียหาย",
            "สาเหตุที่พบบ่อยคือ binlog สะสมโดยไม่มี expire policy, ตาราง temp บวม, หรือ snapshot/backup ค้างอยู่บนดิสก์เดียวกัน",
        ],
        "verify_steps": [
            "df -h <datadir> — ต้องเห็น use% เต็มหรือใกล้เต็ม",
            "SHOW VARIABLES LIKE 'datadir' — ยืนยันว่ากำลังดูพาร์ทิชันที่ถูกตัว",
        ],
        "fix_steps": [
            "df -h และ du -sh <datadir>/* | sort -h | tail — หาว่าอะไรกินที่มากสุด",
            "ถ้าเป็น binlog: SHOW BINARY LOGS แล้ว PURGE BINARY LOGS BEFORE DATE_SUB(NOW(), INTERVAL 3 DAY) — อย่าลบไฟล์ binlog ด้วย rm เพราะ index file จะไม่ตรง",
            "ตั้ง binlog_expire_logs_seconds ให้หมดอายุอัตโนมัติ จะได้ไม่ต้องมาไล่ลบเอง",
            "ตรวจ ibtmp1 (temp tablespace) — ถ้าบวมมากต้อง restart ถึงจะคืนที่",
            "หลังมีที่ว่างแล้วตรวจว่า InnoDB กลับมารับ write ได้จริง: สร้างตารางทดสอบแล้ว INSERT",
        ],
        "docs_url": "https://dev.mysql.com/doc/refman/8.0/en/full-disk.html",
        "applies_to": ">=5.7",
    },
    {
        "id": "mysql.replication_lag",
        "engine": "mysql",
        "frame": "Database",
        "severity": "high",
        "title": "Replica ตามไม่ทัน (replication lag)",
        "symptom_text": (
            "mysql replication lag Seconds_Behind_Master increasing replica delay "
            "slave SQL thread behind relay log read only stale data "
            "replica ตามหลัง primary อ่านข้อมูลเก่า"
        ),
        "error_codes": [],
        "root_cause_chain": [
            "SQL thread บน replica apply event ช้ากว่าที่ primary ผลิต ทำให้ระยะห่างถ่างขึ้นเรื่อยๆ",
            "read query ที่วิ่งไป replica จึงได้ข้อมูลเก่ากว่าความจริง",
            "สาเหตุที่พบบ่อย: replica apply แบบ single thread ในขณะที่ primary เขียนแบบ concurrent, ตารางที่ไม่มี primary key ทำให้ row event ต้อง scan, หรือ I/O ของ replica ช้ากว่า primary",
        ],
        "verify_steps": [
            "SHOW REPLICA STATUS — ดู Seconds_Behind_Master ว่าเพิ่มขึ้นต่อเนื่องไหม (MySQL 8.0.22 ลงมาใช้ SHOW SLAVE STATUS)",
            "ถ้า Seconds_Behind_Master = NULL แปลว่า thread หยุด ไม่ใช่แค่ช้า — ต้องดู Last_Error",
        ],
        "fix_steps": [
            "SHOW REPLICA STATUS\\G — เช็ค Replica_IO_Running / Replica_SQL_Running ว่ายัง Yes ทั้งคู่",
            "ถ้า apply ช้า: เปิด parallel apply — SET GLOBAL replica_parallel_workers = 4 (ขึ้นไป) และใช้ replica_parallel_type = LOGICAL_CLOCK",
            "ตรวจว่าทุกตารางมี primary key — row-based replication บนตารางที่ไม่มี PK ทำให้ replica ต้อง full scan ต่อหนึ่งแถว",
            "เทียบ I/O ของ replica กับ primary — replica ที่ใช้ดิสก์ช้ากว่าจะตามไม่ทันตั้งแต่ต้น",
            "ระหว่างที่ lag ยังสูง ให้ย้าย read ที่ต้องการข้อมูลสดกลับไปที่ primary ชั่วคราว",
        ],
        "docs_url": "https://dev.mysql.com/doc/refman/8.0/en/replication-administration-status.html",
        "applies_to": ">=5.7",
    },
    {
        "id": "mysql.aborted_connection",
        "engine": "mysql",
        "frame": "Network",
        "severity": "medium",
        "title": "Aborted connection / connection ถูกตัดกลางคัน",
        "symptom_text": (
            "mysql Aborted connection to db got timeout reading communication packets "
            "MY-010055 Lost connection to MySQL server during query ERROR 2013 2006 "
            "connection หลุดกลางคัน server has gone away"
        ),
        "error_codes": ["2013", "2006", "MY-010055"],
        "root_cause_chain": [
            "connection ถูกปิดโดยไม่ผ่านขั้นตอน close ปกติ MySQL จึงนับเป็น aborted",
            "ฝั่ง client เห็นเป็น 'Lost connection' หรือ 'server has gone away' กลางคำสั่ง",
            "สาเหตุแยกได้สามทาง: หมดเวลาตาม wait_timeout, packet ใหญ่เกิน max_allowed_packet, หรืออุปกรณ์ระหว่างทาง (firewall/LB) ตัด idle connection ทิ้ง",
        ],
        "verify_steps": [
            "SHOW GLOBAL STATUS LIKE 'Aborted_%' — แยกให้ออกว่าเป็น Aborted_connects (ต่อไม่ติดตั้งแต่แรก มักเป็น auth) หรือ Aborted_clients (ต่อติดแล้วหลุด)",
            "ดู error log ว่ามีข้อความ 'got timeout reading communication packets' หรือไม่",
        ],
        "fix_steps": [
            "SHOW VARIABLES LIKE 'wait_timeout' และ 'interactive_timeout' — เทียบกับ idle timeout ของ connection pool ฝั่ง application; pool ต้องรีไซเคิล connection ก่อน MySQL จะตัด",
            "SHOW VARIABLES LIKE 'max_allowed_packet' — ถ้ามี query ที่ส่ง blob ใหญ่ ต้องเพิ่มค่านี้ทั้งฝั่ง server และ client",
            "ตรวจ idle timeout ของ firewall / load balancer ที่คั่นอยู่ — ค่ามาตรฐานหลายตัวคือ 300 วินาที ซึ่งสั้นกว่า wait_timeout ของ MySQL",
            "เปิด TCP keepalive ที่ connection pool เพื่อไม่ให้ connection ดูเหมือน idle",
            "ถ้า Aborted_connects สูงเป็นหลัก ให้ไปดูเรื่อง credential/host permission แทน — คนละสาเหตุกัน",
        ],
        "docs_url": "https://dev.mysql.com/doc/refman/8.0/en/communication-errors.html",
        "applies_to": ">=5.7",
    },
    {
        "id": "mysql.table_corruption",
        "engine": "mysql",
        "frame": "Hardware",
        "severity": "critical",
        "title": "ตารางเสียหาย / page corruption",
        "symptom_text": (
            "mysql InnoDB Database page corruption on disk failed file read tablespace "
            "table is marked as crashed and should be repaired ERROR 1194 145 "
            "checksum mismatch ตารางเสียหาย อ่านไฟล์ไม่ได้"
        ),
        "error_codes": ["1194", "145", "1034"],
        "root_cause_chain": [
            "InnoDB อ่านหน้า (page) แล้ว checksum ไม่ตรง แปลว่าไฟล์บนดิสก์ไม่ตรงกับที่เขียนไว้",
            "MySQL จะหยุดหรือ crash เพื่อกันไม่ให้ข้อมูลเสียลามไปมากกว่าเดิม",
            "ต้นตอเกือบทั้งหมดอยู่ใต้ระดับ database: ดิสก์กำลังจะเสีย, ไฟดับระหว่างเขียนโดยไม่มี battery-backed cache, หรือ storage layer ที่โกหกเรื่อง fsync",
        ],
        "verify_steps": [
            "ดู error log หา 'Database page corruption' หรือ 'checksum mismatch' พร้อมชื่อ tablespace",
            "smartctl -a /dev/<disk> — ตรวจ Reallocated_Sector_Ct และ Current_Pending_Sector ว่าขึ้นหรือไม่",
        ],
        "fix_steps": [
            "หยุดก่อน อย่าเพิ่งเขียนอะไรเพิ่ม — ทุก write หลังจากนี้ทำให้กู้ยากขึ้น",
            "สำรองไฟล์ datadir ทั้งชุดในสภาพปัจจุบันไว้ก่อนแตะอะไรทั้งสิ้น",
            "ตรวจฮาร์ดแวร์: smartctl -a และ dmesg | grep -i 'i/o error' — ถ้าดิสก์กำลังเสีย ต้องย้ายเครื่องก่อนค่อยกู้",
            "ถ้ามี replica ที่ยังดีอยู่: promote replica แทนการซ่อม primary — เร็วกว่าและเสี่ยงน้อยกว่ามาก",
            "ถ้าไม่มี replica: กู้จาก backup ล่าสุด แล้ว replay binlog ต่อจากจุด backup",
            "innodb_force_recovery ใช้เพื่อ 'ดึงข้อมูลออกมา' เท่านั้น ไม่ใช่เพื่อวิ่งต่อ — ตั้งค่าต่ำสุดที่เปิดติด mysqldump ออกมาแล้วสร้าง instance ใหม่",
        ],
        "docs_url": "https://dev.mysql.com/doc/refman/8.0/en/forcing-innodb-recovery.html",
        "applies_to": ">=5.7",
    },
    {
        "id": "mysql.slow_query_full_scan",
        "engine": "mysql",
        "frame": "Database",
        "severity": "medium",
        "title": "Query ช้าจาก full table scan",
        "symptom_text": (
            "mysql slow query log query_time rows_examined full table scan "
            "no index used type ALL filesort temporary table high latency "
            "query ช้า scan ทั้งตาราง ไม่ได้ใช้ index"
        ),
        "error_codes": [],
        "root_cause_chain": [
            "query ไม่มี index รองรับ InnoDB จึงต้องอ่านทั้งตารางเพื่อตอบคำถามเดียว",
            "เวลาตอบสนองสูงขึ้นตามขนาดตาราง และกินทั้ง CPU และ buffer pool ไปจาก query อื่น",
            "ผลข้างเคียงที่อันตรายกว่าตัวความช้าเอง คือ transaction ยาวขึ้น ทำให้ lock ถูกถือนานขึ้นและเกิด timeout/deadlock ตามมา",
        ],
        "verify_steps": [
            "EXPLAIN <query> — ถ้า type = ALL และ key = NULL คือ full scan จริง",
            "เทียบ rows_examined กับ rows_sent ใน slow log — ต่างกันหลายเท่าคือ scan เกินความจำเป็น",
        ],
        "fix_steps": [
            "เปิด slow log ถ้ายังไม่เปิด: SET GLOBAL slow_query_log = ON; SET GLOBAL long_query_time = 1",
            "EXPLAIN ANALYZE <query> — ดูว่าเวลาหมดไปกับขั้นตอนไหนจริงๆ ไม่ใช่เดาจากแผน",
            "สร้าง composite index ให้ตรงกับ WHERE + ORDER BY ตามลำดับที่ query ใช้จริง",
            "หลีกเลี่ยง SELECT * — ถ้าดึงเฉพาะคอลัมน์ที่อยู่ใน index จะได้ covering index ซึ่งไม่ต้องแตะตารางเลย",
            "หลังเพิ่ม index แล้ว EXPLAIN ซ้ำเพื่อยืนยันว่า optimizer เลือกใช้จริง — การมี index ไม่ได้แปลว่าถูกใช้",
        ],
        "docs_url": "https://dev.mysql.com/doc/refman/8.0/en/slow-query-log.html",
        "applies_to": ">=5.7",
    },
    {
        "id": "mysql.max_allowed_packet",
        "engine": "mysql",
        "frame": "Database",
        "severity": "medium",
        "title": "Packet ใหญ่เกิน max_allowed_packet",
        "symptom_text": (
            "mysql ERROR 1153 Got a packet bigger than max_allowed_packet bytes "
            "ER_NET_PACKET_TOO_LARGE blob insert failed mysqldump import error "
            "ส่งข้อมูลก้อนใหญ่เกินที่ server ยอมรับ"
        ),
        "error_codes": ["1153", "2006"],
        "root_cause_chain": [
            "คำสั่งเดียวส่งข้อมูลก้อนใหญ่กว่า max_allowed_packet เซิร์ฟเวอร์จึงตัด connection ทิ้ง",
            "ฝั่ง client มักเห็นเป็น 'MySQL server has gone away' (2006) ซึ่งชี้ผิดทาง ทำให้ไล่ผิดจุด",
            "พบบ่อยตอน insert blob, bulk insert แถวเยอะในคำสั่งเดียว หรือ restore จาก mysqldump",
        ],
        "verify_steps": [
            "SHOW VARIABLES LIKE 'max_allowed_packet' — เทียบกับขนาดข้อมูลที่กำลังส่ง",
            "ดู error log ฝั่ง server ว่ามี 1153 จริงไหม — ถ้ามีแต่ 2006 ฝั่ง client อาจเป็นคนละสาเหตุ",
        ],
        "fix_steps": [
            "SET GLOBAL max_allowed_packet = 67108864 — มีผลกับ connection ใหม่เท่านั้น connection เดิมต้องต่อใหม่",
            "ตั้งค่าเดียวกันใน my.cnf ใต้ [mysqld] ไม่งั้นหายหลัง restart",
            "ตั้งฝั่ง client ด้วย — mysqldump/mysql มี --max-allowed-packet ของตัวเอง ต้องตั้งทั้งสองฝั่ง",
            "ถ้าเป็น bulk insert: แบ่ง batch ให้เล็กลงจะดีกว่าเพิ่มเพดาน เพราะ packet ใหญ่กิน memory ต่อ connection",
        ],
        "docs_url": "https://dev.mysql.com/doc/refman/8.0/en/packet-too-large.html",
        "applies_to": ">=5.7",
    },
    {
        "id": "mysql.oom_killed",
        "engine": "mysql",
        "frame": "Hardware",
        "severity": "critical",
        "title": "mysqld ถูก OOM killer ฆ่า",
        "symptom_text": (
            "mysql mysqld killed oom-killer Out of memory Cannot allocate memory "
            "InnoDB mmap failed signal 9 restart loop kernel out of memory "
            "หน่วยความจำไม่พอ mysqld ถูก kernel ฆ่าแล้ว restart"
        ),
        "error_codes": ["12"],
        "root_cause_chain": [
            "kernel ต้องการ memory คืนแล้วเลือกฆ่า mysqld เพราะเป็น process ที่กินมากที่สุด",
            "MySQL ถูกปิดกะทันหันโดยไม่ได้ flush จึงต้องทำ crash recovery ตอนเปิดใหม่ ซึ่งกินเวลาและ I/O",
            "ต้นตอคือผลรวมของ buffer pool + memory ต่อ session × จำนวน connection สูงสุด เกิน RAM จริงของเครื่อง",
        ],
        "verify_steps": [
            "dmesg -T | grep -i 'killed process' — ต้องเห็นชื่อ mysqld พร้อมเวลาที่ตรงกับตอน service หาย",
            "ดู error log ว่าเปิดขึ้นมาด้วย 'Starting crash recovery' หรือไม่ — ยืนยันว่าถูกฆ่าจริง ไม่ได้ปิดปกติ",
        ],
        "fix_steps": [
            "dmesg -T | grep -i oom — ยืนยันเวลาและ process ที่ถูกฆ่า",
            "คำนวณเพดานจริง: innodb_buffer_pool_size + (per-session buffers × max_connections) ต้องน้อยกว่า RAM โดยเหลือให้ OS",
            "ลด innodb_buffer_pool_size ให้เหลือราว 50-70% ของ RAM สำหรับเครื่องที่รัน MySQL อย่างเดียว",
            "ลด max_connections ให้ตรงกับที่ใช้จริง (ดูจาก Max_used_connections) — เพดานที่ตั้งไว้สูงเกินคือหนี้ memory ที่รอวันทวง",
            "ตรวจว่ามี process อื่นบนเครื่องเดียวกันแย่ง RAM หรือไม่ — DB ควรอยู่ลำพัง",
            "ถ้าปรับแล้วยังไม่พอ ต้องเพิ่ม RAM — swap ไม่ช่วย เพราะ MySQL ที่ต้อง swap จะช้าจนใช้งานไม่ได้อยู่ดี",
        ],
        "docs_url": "https://dev.mysql.com/doc/refman/8.0/en/memory-use.html",
        "applies_to": ">=5.7",
    },
]
