# คู่มือปรับ logsim ให้ simulate จาก topology จริง

> เป้าหมาย: ให้ log ที่ logsim ยิงเข้า aiops (`POST /ingest`) ชี้กลับไปหา node ใน
> topology ที่ upload ไว้ได้ → ระบบจะเปิดใช้ **topology propagation forecast**
> (ทำนายการลามข้าม host) และดึง **version knowledge** มาเป็นหลักฐานให้ judge
>
> อัปเดตล่าสุด: 2026-07-09 (อ้างอิง topology snapshot ใน tenant `internal`)

---

## 1. กติกาที่ logsim ต้องทำตาม

### 1.1 field `host` (สำคัญที่สุด)

`host` ในแต่ละ log entry ต้องเป็นค่าใดค่าหนึ่งต่อไปนี้ของ node ใน topology:

| แบบ | ตัวอย่าง | หมายเหตุ |
|---|---|---|
| **node_id** (แนะนำ) | `db-pay-pri` | ชัดเจนสุด ไม่มีทางชนกัน |
| label | `Payment DB Primary` | ใช้ได้ แต่ label ซ้ำข้ามสาขาได้ (เช่น "POS Controller" มี 3 ตัว → จะ match ตัวเดียว) |
| IP | `10.0.3.10` | ใช้ได้เฉพาะ node ที่มี IP เดี่ยว (IP แบบ range/masked ใช้ไม่ได้) |

ชื่ออื่นนอกเหนือจากนี้ (เช่น `mysql_synthetic`) จะวิเคราะห์ per-host ได้ตามปกติ
แต่**ไม่เข้าร่วม propagation** — ระบบไม่รู้ว่ามันอยู่ตรงไหนของกราฟ

### 1.2 `tenant_id`

ใช้ `logsim` ต่อไปได้ — backend มี fallback: tenant ที่ไม่มี topology ของตัวเอง
จะใช้ topology ของ `internal` อัตโนมัติ

### 1.3 เนื้อ log ควรตรงกับ software ของ node

Judge เห็น version knowledge ของ node นั้นๆ (research สะสมจากอินเทอร์เน็ต)
ถ้าเนื้อ log ขัดกับ software จริง หลักฐานจะตีกันเอง เช่น:

- `db-pay-pri` เป็น **Oracle 19c** → ควรเป็น `ORA-00060: deadlock detected`,
  `ORA-12170: TNS:Connect timeout` — **ไม่ใช่** MySQL deadlock
- `db-inv` / `reg-db-*` เป็น **MySQL 8.0** → `Lock wait timeout exceeded`,
  `Too many connections`, `MySQL server has gone away`
- `redis-pay` เป็น **Redis 7** → `OOM command not allowed`, `maxmemory reached`,
  `evicting keys`
- `pgw-pri-*` / `reg-sw-*` เป็น **RHEL 8** → systemd, OOM killer, disk I/O error
- `br-posctrl-*` เป็น **Windows Server 2019** → Event Log style, service crash

---

## 2. รายชื่อ node ที่ใช้เป็น log source ได้ (จาก topology จริง)

> คอลัมน์ `node_id` คือค่าที่แนะนำให้ใส่ใน field `host`
> (ตัด node ประเภท client — POS terminal / EDC / CCTV / external network — ออก
> เพราะปกติไม่ส่ง log ตรงเข้าระบบ)

### Database / Cache / Broker

| node_id | label | IP | software | type |
|---|---|---|---|---|
| `db-pay-pri` | Payment DB Primary | `10.0.3.10` | Oracle 19c | db |
| `db-pay-rep` | Payment DB Replica | `10.0.3.11` | Oracle 19c | db |
| `dr-db-pay` | DR Payment DB | `10.1.3.10` | Oracle 19c | db |
| `db-inv` | Inventory DB | `10.0.3.20` | MySQL 8.0 | db |
| `db-loyalty` | Loyalty DB | `10.0.3.30` | PostgreSQL 15 | db |
| `redis-pay` | Redis Payment Cache | `10.0.4.10` | Redis 7 | db |
| `kafka` | Kafka Cluster | `10.0.4.20` | Kafka 3.5 | db |
| `reg-db-bkk` | Bangkok Hub Local DB | `10.10.3.10` | MySQL 8.0 | db |
| `reg-db-cen` | Central Hub Local DB | `10.11.3.10` | MySQL 8.0 | db |
| `reg-db-nth` | North Hub Local DB | `10.12.3.10` | MySQL 8.0 | db |
| `reg-db-nth2` | NE Hub Local DB | `10.13.3.10` | MySQL 8.0 | db |
| `reg-db-est` | East Hub Local DB | `10.14.3.10` | MySQL 8.0 | db |
| `reg-db-sth` | South Hub Local DB | `10.15.3.10` | MySQL 8.0 | db |
| `br-localdb-hm` | Local POS DB | `192.168.20.200` | SQLite/PostgreSQL | db |
| `br-localdb-sm` | Local POS DB | (IP ซ้ำ — ใช้ node_id) | SQLite/PostgreSQL | db |
| `br-localdb-mini` | Local POS DB | (IP ซ้ำ — ใช้ node_id) | SQLite/PostgreSQL | db |

### Server / Load balancer

| node_id | label | IP | software | type |
|---|---|---|---|---|
| `pgw-pri-1` | Payment GW 1 | `10.0.1.11` | RHEL 8 | server |
| `pgw-pri-2` | Payment GW 2 | `10.0.1.12` | RHEL 8 | server |
| `pgw-pri-3` | Payment GW 3 | `10.0.1.13` | RHEL 8 | server |
| `dr-pgw` | DR Payment GW | `10.1.1.10` | RHEL 8 | server |
| `pos-mgmt` | POS Mgmt Server | `10.0.2.10` | RHEL 8 | server |
| `inv-api` | Inventory API | `10.0.2.20` | Ubuntu 22.04 | server |
| `loyalty-api` | Loyalty API | `10.0.2.30` | Ubuntu 22.04 | server |
| `hsm` | HSM (Thales) | `10.0.5.10` | Thales HSM | server |
| `dc-lb-pri` | DC Load Balancer | `10.0.0.2` | F5 BIG-IP | lb |
| `reg-sw-bkk` | Bangkok Hub Switch | `10.10.1.10` | RHEL 8 | server |
| `reg-sw-cen` | Central Hub Switch | `10.11.1.10` | RHEL 8 | server |
| `reg-sw-nth` | North Hub Switch | `10.12.1.10` | RHEL 8 | server |
| `reg-sw-nth2` | NE Hub Switch | `10.13.1.10` | RHEL 8 | server |
| `reg-sw-est` | East Hub Switch | `10.14.1.10` | RHEL 8 | server |
| `reg-sw-sth` | South Hub Switch | `10.15.1.10` | RHEL 8 | server |
| `br-posctrl-hm` | POS Controller | `192.168.20.10` | Windows Server 2019 | server |
| `br-posctrl-sm` | POS Controller | (IP ซ้ำ — ใช้ node_id) | Windows Server 2019 | server |
| `br-posctrl-mini` | POS Controller | (IP ซ้ำ — ใช้ node_id) | Windows Server 2019 | server |

### Network (firewall / router / switch)

| node_id | label | IP | software | type |
|---|---|---|---|---|
| `dc-fw-pri` | DC Firewall Primary | `10.0.0.1` | Cisco ASA 5585 | router |
| `dr-fw` | DR Firewall | `10.1.0.1` | Cisco ASA 5585 | router |
| `dc-wan-core` | WAN Core Router | `10.0.0.10` | Cisco ASR 1001 | router |
| `reg-fw-bkk` | Bangkok Hub FW | `10.10.0.1` | Cisco ISR 4451 | router |
| `reg-fw-cen` | Central Hub FW | `10.11.0.1` | Cisco ISR 4451 | router |
| `reg-fw-nth` | North Hub FW | `10.12.0.1` | Cisco ISR 4451 | router |
| `reg-fw-nth2` | NE Hub FW | `10.13.0.1` | Cisco ISR 4451 | router |
| `reg-fw-est` | East Hub FW | `10.14.0.1` | Cisco ISR 4451 | router |
| `reg-fw-sth` | South Hub FW | `10.15.0.1` | Cisco ISR 4451 | router |
| `br-fw-hm` | Hypermarket Branch FW | `192.168.20.1` | Fortinet 60F | router |
| `br-fw-sm` | Supermarket Branch FW | `192.168.21.1` | Fortinet 60F | router |
| `br-fw-mini` | Mini Big C Branch FW | `192.168.22.1` | Fortinet 60F | router |
| `br-sw-hm` | Hypermarket Branch Core SW | `192.168.20.2` | Cisco C2960 | router |
| `br-sw-sm` | Supermarket Branch Core SW | (IP ซ้ำ — ใช้ node_id) | Cisco C2960 | router |
| `br-sw-mini` | Mini Big C Branch Core SW | (IP ซ้ำ — ใช้ node_id) | Cisco C2960 | router |

### Service layer (จาก service_dependency graph)

| node_id | label | software |
|---|---|---|
| `pay-switch` | Payment Switch | Java 17 |
| `pay-gateway` | Payment Gateway | RHEL 8 / Java |
| `pay-router` | Payment Router | Java 17 |
| `pay-db` | Payment DB (Oracle) | Oracle 19c RAC |
| `pay-cache` | Payment Cache | Redis 7 Cluster |
| `fraud-svc` | Fraud Detection | Python 3.11 |
| `hsm-svc` | HSM Service | Thales HSM |
| `pos-ctrl-svc` | POS Controller Svc | C# .NET 6 |
| `inv-svc` / `inv-db` | Inventory Service / DB | Spring Boot / MySQL 8.0 |
| `loyalty-svc` / `loyalty-db` | Loyalty Service / DB | Node.js 20 / PostgreSQL 15 |
| `receipt-svc` | Receipt/e-Tax Service | Python 3.11 |
| `api-gw` | API Gateway | Kong / Nginx |
| `auth-svc` | Auth/Token Service | Keycloak |
| `config-svc` | Config Service | Spring Config |
| `offline-db` | Offline DB (fallback) | SQLite |
| `log-collect` / `monitor` | Log Collector / Monitoring | Fluent Bit / Prometheus |

> ⚠️ **ชื่อซ้ำความหมายข้าม layer**: `pay-db` ≈ `db-pay-pri`, `inv-db` ≈ `db-inv`,
> `pay-gateway` ≈ `pgw-pri-*` — เพราะ GodEye ส่ง network กับ service graph แยกกัน
> ถ้าอยากเห็น cascade ยาวๆ **ให้ยิงชื่อฝั่ง network** (`db-pay-pri`, `pgw-pri-1`, ...)
> เพราะกราฟฝั่งนั้นลึกกว่า

---

## 3. Scenario แนะนำ (เรียงตามความน่าดูของ cascade)

### S1 — Payment DB ล่ม (cascade ใหญ่สุด)
- ยิง Oracle errors ใส่ `db-pay-pri` จน health ต่ำ
- คาดหวังใน `propagation_forecast`: `pgw-pri-1/2/3` critical ใน ~8 นาที →
  `dc-lb-pri` ~9 → `dc-fw-pri` ~15 → WAN/ช่องทางธนาคาร ~22
- log ที่เหมาะ: `ORA-00060 deadlock`, `ORA-12537 TNS:connection closed`,
  `archiver error. Connect internal only, until freed`

### S2 — Redis cache เสื่อม (partial impact)
- ยิง `OOM command not allowed when used memory > 'maxmemory'` ใส่ `redis-pay`
- คาดหวัง: Payment GW โดนกดแบบช้าๆ (edge weight 0.5 — ไม่ critical)
  เห็นความต่างชัดจาก S1

### S3 — DB สาขาเดียว (scope แคบ)
- ยิง MySQL errors ใส่ `db-inv`
- คาดหวัง: `inv-api` โดนตัวเดียว — พิสูจน์ว่า engine ไม่ over-predict

### S4 — Regional hub ล่ม (ลามเชิงภูมิศาสตร์)
- ยิง RHEL/hardware errors ใส่ `reg-sw-bkk` หรือ firewall errors ใส่ `reg-fw-bkk`
- คาดหวัง: branch FW ใต้ hub นั้นโดนหางเลข

### S5 — หลาย host พร้อมกัน (ของจริงมักเป็นแบบนี้)
- `db-pay-pri` health แย่ + `redis-pay` แย่พร้อมกัน
- คาดหวัง: forecast รวมแรงกดสองทาง GW ล้มเร็วกว่า S1 เดี่ยวๆ

---

## 4. วิธีตรวจผลหลังยิง

```bash
# 1) forecast ใน response ล่าสุด
curl -s http://<aiops-host>:8200/api/results?limit=1   # ดู id ล่าสุด
curl -s http://<aiops-host>:8200/api/results/<id> | jq '.payload.propagation_forecast'

# 2) log ฝั่ง backend ต้องมีบรรทัดนี้ (ถ้า match สำเร็จ)
grep "Propagation" logs/backend.log
# → "Propagation: N downstream incidents predicted (seeds=M)"

# 3) judge เห็นหลักฐานไหม — ดู root_cause_chain ของ host ต้นตอ
#    ควรอ้างถึง downstream impact / version knowledge
```

สิ่งที่ควรเห็นเมื่อทุกอย่างต่อติด:
1. `propagation_forecast.incidents` ไม่ว่าง มี `caused_by` chain ชี้กลับต้นตอ
2. `root_cause_chain` ของ judge อ้างถึงการลาม (เช่น "จะทำให้ Payment GW timeout ใน ~8 นาที")
3. host ที่ health < 70 จะดัน research profile ของตัวเองขึ้นหัวคิว —
   ดูได้ที่ `GET /api/knowledge`

---

## 5. รูปแบบ payload ที่ /ingest รับ (เดิม ไม่เปลี่ยน)

```json
{
  "tenant_id": "logsim",
  "entries": [
    {
      "_time": "2026-07-09T12:00:00Z",
      "host": "db-pay-pri",
      "service": "oracle",
      "severity_text": "error",
      "message": "ORA-00060: deadlock detected while waiting for resource"
    }
  ]
}
```
