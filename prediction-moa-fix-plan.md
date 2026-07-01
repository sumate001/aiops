# AIOps Predictor × MoA Integration — Fix Plan

Repo: sumate001/aiops (branch main)
สำหรับ: ส่งต่อให้ Claude Code CLI implement โดยตรง
สถานะปัจจุบัน: GodEyes AIOps MoA pipeline (A1 Rule+IF → A3 MiroFish → AA Synthesizer → A2 Perplexica) รันได้ แต่ predictor (app/services/predictor.py) เป็น isolated node ที่ไม่ได้เข้าร่วม consensus loop จริง ทำให้ prediction quality ต่ำกว่าที่ควรจะเป็น

---

## 0. Context — ปัญหาที่ตรวจพบ (สรุปจาก audit)

P1 | Predictor (trend + prediction) คำนวณใน Phase 1 แต่ไม่เคยถูกส่งเข้า Phase 3 (AA) — AA ไม่เคย judge ผลของมัน | app/routers/analyze.py _phase3_aa
P2 | generate_prediction() ไม่รับ anomalies (A1 IF detail) เป็น input — เห็นแค่ current_health ที่ IF signal ถูก dilute ไปแล้ว | app/services/predictor.py
P3 | มี confidence 2 ค่าคนละสูตร (Synthesis.confidence vs PredictionInfo.confidence) ไม่ reconcile กัน | app/services/synthesizer.py + predictor.py
P4 | save_window_stat() persist ก่อน IF ปรับ health_score → baseline/trend/z-score เพี้ยนสะสมทุกครั้งที่ IF ตรวจเจอ anomaly | app/routers/analyze.py _phase1_a1
P5 | ไม่มี Prometheus metric ของ predictor เลย — backtest/validate accuracy ย้อนหลังไม่ได้ | app/services/metrics.py
P6 | Linear slope ไม่ smooth, ไม่ weight ตามความใหม่, SLOPE_THRESH=0.02 hardcode ไม่ปรับตาม host | predictor.py _linear_slope, analyze_trend
P7 | estimated_incident_in สมมติ health degrade เป็นเส้นตรง — ไม่จริงสำหรับ cascading failure | predictor.py generate_prediction
P8 | Fingerprint match ให้ risk +20 แบบ flat ไม่สนสัดส่วน signal ที่ active จริง | predictor.py generate_prediction
P9 | MiroFish (keyword/signal scoring) กับ Predictor fingerprint (POS KB) สแกน signal ชุดเดียวกันแยกอิสระ ไม่ reconcile ผล | mirofish.py vs predictor.py

---

## 1. เป้าหมาย

1. Predictor กลายเป็น input ของ AA Synthesizer ไม่ใช่ output ที่แปะเข้า response เฉยๆ
2. เหลือ confidence ค่าเดียว ต่อ host ที่มีความหมายชัดเจน (หรือถ้าเก็บ 2 ค่า ต้องมี field อธิบายความสัมพันธ์)
3. Data ที่ persist ลง SQLite ต้องสะท้อนค่าที่ "ถูกต้องที่สุด ณ เวลานั้น" (post-IF)
4. มี metric ให้ backtest แม่นยำของ prediction ได้จริงใน Grafana
5. ปรับ heuristic ให้ robust ขึ้น (smoothing, adaptive threshold, non-linear ETA, proportional fingerprint score) — ทำได้ทีหลังกลุ่ม P1-P5 เพราะ P1-P5 คือ structural bug ที่กระทบความถูกต้องมากกว่า

ลำดับความสำคัญ: P4 (data corruption bug) → P1+P2 (architecture) → P3 (confidence) → P5 (observability) → P6-P9 (heuristic quality)

---

## 2. Fix P4 — Persist window_stat หลัง IF ปรับ health_score (ทำก่อน เพราะกระทบข้อมูลทุกอย่างที่ตามมา)

ไฟล์: app/routers/analyze.py, ฟังก์ชัน _phase1_a1

ปัจจุบัน: save_window_stat() ถูกเรียกก่อนบล็อก if config.log_ml.enabled: ที่ recompute st.health_score

แก้ไข: ย้าย save_window_stat() ไปหลังบล็อก IF ทั้งหมด (หลังบรรทัดที่ recompute st.health_score ด้วย anomaly_scores ที่รวม IF แล้ว) ใช้ st.health_score ตัวล่าสุดเสมอตอน build WindowStat

Acceptance: เขียน unit test จำลอง host ที่ log-ml ตอบ is_anomaly=True แล้ว assert ว่า WindowStat.health_score ที่ถูก save มีค่า "ลดลง" ตาม anomaly_penalty ไม่ใช่ค่าก่อนปรับ

Migration note: ข้อมูลเก่าใน SQLite ที่ persist มาก่อน fix นี้จะยัง underestimate อยู่ — ไม่ต้อง backfill (เสี่ยง corrupt เพิ่ม) แค่ปล่อยให้ trend window ใหม่ๆ ค่อยๆ แทนที่ของเก่า (get_recent_windows(limit=15))

---

## 3. Fix P1+P2 — ทำให้ Predictor เป็น Agent ที่ AA เห็นและ judge

เป้าหมายสถาปัตยกรรม: เปลี่ยนจาก

Phase1(A1) → trend+prediction (dead-end, ไม่มีใครอ่าน)
Phase2(A3) ─┐
            ├→ Phase3(AA) → synthesis
Phase1(A1) ─┘

เป็น

Phase1(A1) → anomalies + trend + prediction
Phase2(A3) → mirofish_frames
                    ↓
Phase3(AA) ← รับ anomalies + mirofish_frames + trend + prediction ทั้งหมด
           → synthesis (root_cause_chain + unified confidence + fix_steps)

### 3.1 predictor.py::generate_prediction() — เพิ่ม parameter anomalies

def generate_prediction(
    host: str,
    current_health: float,
    trend: TrendInfo,
    error_count: int,
    warn_count: int,
    entry_count: int,
    anomalies: list[dict] | None = None,   # ← ใหม่: A1 anomalies ดิบ (rule + IF)
    top_error_msgs: list[str] | None = None,
) -> PredictionInfo:
    anomalies = anomalies or []
    ...
    # ใช้ anomalies โดยตรงแทนที่จะพึ่ง current_health อย่างเดียว
    if_anomalies = [a for a in anomalies if a["metric"] == "isolation_forest"]
    if if_anomalies:
        a = if_anomalies[0]
        signals.append(f"A1-IF flagged: score={a['score']:.2f} severity={a['severity']}")
        risk += 15 if a["severity"] == "high" else 8

เรียกใน analyze.py::_phase1_a1: ส่ง anomalies=[a.model_dump() for a in st.anomalies] เข้าไปด้วย

### 3.2 synthesizer.py::synthesize() — เพิ่ม parameter trend, prediction

async def synthesize(
    host: str,
    health_score: float,
    anomalies: list[dict],
    mirofish_frames: list[dict],
    trend: dict | None = None,        # ← ใหม่
    prediction: dict | None = None,   # ← ใหม่
    ...
) -> SynthesisResult:

- _rule_synthesis(): ถ้า prediction.risk_level in ("high", "critical") ให้เพิ่ม chain item เช่น
  f"[Predictor] {prediction['risk_level']} risk — {prediction.get('estimated_incident_in', 'timing unknown')}"
  และรวม matched_fingerprint เข้า root_cause_chain ถ้ามี
- _build_judge_prompt(): เพิ่ม block "Predictor (trend+risk):" เข้า prompt เพื่อให้ LLM judge เห็นหลักฐานฝั่ง predictive ด้วย ไม่ใช่แค่ anomaly กับ mirofish

### 3.3 analyze.py::_phase3_aa — ส่ง st.trend, st.prediction เข้า synthesizer.synthesize()

st.synth_result = await synthesizer.synthesize(
    host=st.hostname,
    health_score=st.health_score,
    anomalies=[a.model_dump() for a in st.anomalies],
    mirofish_frames=st.mirofish_frames,
    trend=st.trend.model_dump() if st.trend else None,
    prediction=st.prediction.model_dump() if st.prediction else None,
    ...
)

Acceptance: สร้าง test case ที่ predictor คืน risk_level="critical" แต่ MiroFish/IF เงียบ (ไม่มี anomaly) → assert ว่า root_cause_chain ที่ AA สร้างต้อง mention predictor signal (ไม่ถูกมองข้าม)

---

## 4. Fix P3 — Unify confidence

ตัดสินใจ: ใช้แนวทาง "AA เป็นเจ้าของ confidence สุดท้ายค่าเดียว" — PredictionInfo.confidence เปลี่ยนความหมายเป็น predictor's internal confidence in its own risk estimate (ไม่ใช่ confidence ของทั้งระบบ) ส่วน Synthesis.confidence คือ overall confidence ที่ AA คำนวณโดยรวม input จาก predictor เข้าไปด้วยแล้ว (ตาม fix ข้อ 3)

### 4.1 Rename field ให้ชัดเจนไม่ให้สับสน (breaking change — แจ้ง frontend ด้วย)

app/models/response.py:
class PredictionInfo(BaseModel):
    risk_level: str
    self_confidence: float   # เดิมชื่อ confidence — เปลี่ยนชื่อกันสับสนกับ Synthesis.confidence
    ...

### 4.2 _rule_synthesis() ใน synthesizer.py ปรับสูตร confidence ให้รวม predictor เข้าไปเป็น 3 องค์ประกอบ

top_relevance   = top["relevance"] if top else 0.0
predictor_conf  = (prediction or {}).get("self_confidence", 0.0)
confidence = min(1.0, top_relevance * 0.45 + max_anomaly_score * 0.30 + predictor_conf * 0.25)

(น้ำหนักปรับได้ — แต่ต้องมีเอกสารอธิบายว่าทำไมเลือกสัดส่วนนี้ อย่าลืม backtest ทีหลัง fix ข้อ 5 เสร็จ)

### 4.3 Frontend (frontend/pages/results.tsx) — อัปเดต field name ที่แสดงผล ให้ label ชัดว่าตัวไหนคือ "overall confidence" (AA) ตัวไหนคือ "predictor self-confidence"

Acceptance: ในหน้า results UI ต้องไม่มี 2 ตัวเลข confidence ที่ label เดียวกันหรือไม่มี label เลย

---

## 5. Fix P5 — เพิ่ม Prometheus metrics สำหรับ predictor (เพื่อ backtest ได้จริง)

ไฟล์: app/services/metrics.py

prediction_risk = Gauge(
    "godeyes_prediction_risk_score",
    "Predictor risk score (0-100)",
    ["host", "tenant_id"],
)

prediction_self_confidence = Gauge(
    "godeyes_prediction_self_confidence",
    "Predictor self-confidence in its own risk estimate (0-1)",
    ["host", "tenant_id"],
)

prediction_risk_level = Gauge(
    "godeyes_prediction_risk_level",
    "Predictor risk level encoded: low=1 medium=2 high=3 critical=4",
    ["host", "tenant_id"],
)

trend_slope = Gauge(
    "godeyes_trend_slope_per_hour",
    "Error rate trend slope per hour",
    ["host", "tenant_id"],
)

# ── สำหรับ backtest: บันทึกว่า prediction นี้ "ตรง" กับ incident จริงไหม ──
prediction_outcome_total = Counter(
    "godeyes_prediction_outcome_total",
    "Prediction vs actual outcome (for backtest)",
    ["host", "tenant_id", "predicted_risk_level", "actual_outcome"],  # actual_outcome: incident | no_incident
)

record_analysis() เพิ่ม loop set ค่าตาม h.prediction และ h.trend

### 5.1 Feedback loop endpoint (ใหม่)

เพิ่ม POST /api/feedback ใน app/routers/ — รับ {host, tenant_id, window_from, actual_outcome: "incident"|"no_incident", incident_type?: str} จาก operator (manual) หรือจาก incident-management webhook (future) แล้ว increment prediction_outcome_total — นี่คือ hook แรกที่ทำให้เริ่มเก็บ ground truth ได้ ถึงจะยัง manual อยู่ก็ตาม

Acceptance: Grafana panel ใหม่ "Prediction Risk vs Actual Outcome" แสดง precision ของแต่ละ risk_level ได้จาก prediction_outcome_total

---

## 6. Fix P6-P9 — Heuristic quality (ทำหลัง P1-P5 เสถียรแล้ว)

### 6.1 P6 — Exponential-weighted slope + adaptive threshold

predictor.py::_linear_slope → เพิ่มน้ำหนัก recency (เช่น weight = 0.9 ** (n-1-i) ให้ window ล่าสุดมีน้ำหนักสูงสุด) และแทน SLOPE_THRESH = 0.02 คงที่ ด้วยค่าที่คำนวณจาก std(error_rates) ของ host นั้นเอง (เช่น threshold = max(0.02, 1.5 * std(error_rates))) เพื่อลด false "rising" จาก host ที่ noisy โดยธรรมชาติ

### 6.2 P7 — Non-linear ETA ด้วย exponential extrapolation

แทนการ fit เส้นตรงกับ health_score ให้ fit log(health_score) แทน (exponential decay model) หรืออย่างน้อยเพิ่ม guard: ถ้า slope ล่าสุด (3 window สุดท้าย) ชันกว่า slope โดยรวมมาก (>2x) ให้ใช้ slope ล่าสุดแทนในการประมาณเวลา (จับ acceleration)

### 6.3 P8 — Fingerprint match แบบ proportional

active_ratio = len(active) / len(matched_fp["required_signals"] + matched_fp["supporting_signals"])
risk += round(20 * active_ratio)

### 6.4 P9 — Reconcile MiroFish frame กับ Predictor fingerprint

เพิ่ม field related_frame ใน POS fingerprint definitions (app/knowledge/pos.py) ที่ map แต่ละ fingerprint ไปยัง MiroFish frame ที่เกี่ยวข้อง (เช่น payment_terminal_offline → Network) แล้วใน _rule_synthesis() ถ้า fingerprint match แต่ frame ที่ related ไม่ใช่ top frame จาก MiroFish ให้เติม chain item เตือนความไม่สอดคล้อง เช่น "⚠ Predictor fingerprint suggests Network but MiroFish top frame is Database — evidence conflict, review manually"

---

## 7. ลำดับการ implement (สำหรับ Claude Code CLI)

1. [ ] P4 — ย้าย save_window_stat() + unit test
2. [ ] P1/P2 — เพิ่ม anomalies param ใน predictor, เพิ่ม trend/prediction param ใน synthesizer, wire ผ่าน analyze.py
3. [ ] P3 — rename confidence→self_confidence, ปรับสูตร _rule_synthesis confidence, อัปเดต frontend labels
4. [ ] P5 — เพิ่ม metrics ใหม่ 5 ตัว + /api/feedback endpoint + Grafana panel
5. [ ] P6-P9 — heuristic improvements ทีละตัว พร้อม test เทียบ before/after บน LogSim scenario เดิม (mysql_cascade เป็น baseline regression test)

## 8. Test plan

- รัน LogSim scenario mysql_cascade ก่อน/หลัง fix แต่ละตัว เทียบ risk_level, estimated_incident_in, และ root_cause_chain ว่าเปลี่ยนไปในทางที่ดีขึ้น (ไม่ flip-flop, ETA สมเหตุสมผลกว่าเดิม)
- Unit test สำหรับ P4 (health_score persist ถูกต้อง), P1/P2 (AA เห็น predictor signal), P3 (ไม่มี confidence ขัดแย้งกันใน response เดียว)
- เพิ่ม scenario ใหม่ใน LogSim ที่จำลอง "slow drift ไม่มี spike" เพื่อทดสอบ P6 (adaptive threshold) โดยเฉพาะ
