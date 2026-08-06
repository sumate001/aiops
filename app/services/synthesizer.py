"""
AA — LLM-as-Judge Synthesizer
รับ output A1 (isolation_forest anomalies) + A3 (MiroFish frames)
→ root_cause_chain, confidence, fix_steps

Rule-based path เสมอ (instant, no LLM dependency)
LLM enrichment path เป็น opt-in (ถ้า Ollama up)
"""

import json
import logging
from dataclasses import dataclass, field

from app.knowledge.pos import POS_FAILURE_FINGERPRINTS

logger = logging.getLogger(__name__)

_FINGERPRINT_FRAME: dict[str, str] = {
    fp["name"]: fp["related_frame"] for fp in POS_FAILURE_FINGERPRINTS if fp.get("related_frame")
}

# ─── Frame playbooks (rule-based fix steps) ──────────────────────────────────

_FRAME_PLAYBOOK: dict[str, list[str]] = {
    "Security": [
        "Audit authentication logs for brute-force patterns",
        "Block offending IPs at firewall or WAF",
        "Force password reset for affected accounts",
        "Enable MFA if not already active",
        "Review privilege assignments and service account permissions",
    ],
    "Database": [
        "Check for long-running queries with SHOW PROCESSLIST",
        "Kill deadlocked transactions and review lock contention",
        "Increase connection pool size or reduce connection timeout",
        "Run ANALYZE TABLE to refresh query planner statistics",
        "Review slow query log and add missing indexes",
    ],
    "Network": [
        "Ping payment gateway and upstream endpoints for reachability",
        "Check WAN link utilization and latency on MPLS/SD-WAN",
        "Review firewall rules for recently changed ACLs",
        "Inspect DNS resolution time and TTLs",
        "Switch to backup payment gateway if primary unreachable",
    ],
    "Hardware": [
        "Check disk I/O saturation with iostat or iotop",
        "Review dmesg for kernel-level hardware errors",
        "Check disk health via smartctl -a /dev/sd*",
        "Free memory: restart heavy services or add swap",
        "Inspect temperature and fan status in IPMI/BMC",
    ],
    "Software": [
        "Check systemd service status and journal for crash details",
        "Review application logs for uncaught exceptions or OOM",
        "Restart the failing service and monitor restart frequency",
        "Check for dependency version mismatches or missing libraries",
        "Roll back the most recent deployment if crash began post-deploy",
    ],
}

_DEFAULT_PLAYBOOK: list[str] = [
    "Review system logs for the affected host",
    "Check service health and restart if necessary",
    "Escalate to on-call engineer if issue persists",
]


# ─── Root-cause chain builder (rule-based) ───────────────────────────────────

@dataclass
class SynthesisResult:
    root_cause_chain: list[str]
    confidence: float         # 0.0–1.0
    fix_steps: list[str]
    method: str               # "rule" | "llm"
    top_frame: str | None = None
    top_frame_lens: str | None = None
    anomaly_methods: list[str] = field(default_factory=list)
    reasoning: str | None = None
    memory_refs: list[str] = field(default_factory=list)
    memory_influenced: bool = False
    playbook_refs: list[str] = field(default_factory=list)
    playbook_influenced: bool = False


# Confidence boost tiers for a verified recalled case.
#
# The plan specified 0.80 / 0.65 on the assumption that similarity spans 0-1.
# It doesn't: multilingual-e5-small never scores below ~0.72, so 0.65 would fire
# on literally everything and 0.80 on plainly unrelated text. Measured on this
# model — identical 0.96, same issue different wording 0.85, same engine other
# issue 0.83, unrelated 0.79, gibberish 0.78 — these are the equivalent cuts.
MEM_SIM_STRONG = 0.90    # effectively the same symptom
MEM_SIM_MODERATE = 0.84  # same issue, described differently


def _rule_synthesis(
    host: str,
    health_score: float,
    anomalies: list[dict],
    mirofish_frames: list[dict],
    trend: dict | None = None,
    prediction: dict | None = None,
    memory_hits: list | None = None,
) -> SynthesisResult:
    """
    Pure-rule synthesis — no LLM, instant.
    Weighs MiroFish relevance + anomaly severity to produce root_cause_chain.

    Memory is handled here too, not only on the LLM path: when the judge model
    fails and we fall back to rules, recalled cases have to keep showing up.
    Otherwise memory disappears exactly when the system is least reliable, and
    nothing in the output says so.
    """
    memory_hits = memory_hits or []
    # Filter relevant frames
    relevant = [f for f in mirofish_frames if f["relevance"] > 0]
    top = relevant[0] if relevant else None

    # Anomaly methods + max score
    anomaly_methods = [a["metric"] for a in anomalies]
    max_anomaly_score = max((a["score"] for a in anomalies), default=0.0)

    # Build root_cause_chain
    chain: list[str] = []

    if top:
        frame_name = top["frame"]
        relevance = top["relevance"]
        kws = top["top_keywords"]
        kw_str = f" (signals: {', '.join(kws[:3])})" if kws else ""
        chain.append(
            f"[{frame_name}] Primary domain: {top['lens'].replace('_',' ').title()}"
            f" — relevance {relevance:.0%}{kw_str}"
        )

    if anomalies:
        if_anomalies = [a for a in anomalies if a["metric"] == "isolation_forest"]
        rule_anomalies = [a for a in anomalies if a["metric"] != "isolation_forest"]

        if if_anomalies:
            a = if_anomalies[0]
            chain.append(
                f"[A1-IF] Isolation Forest flagged abnormal pattern "
                f"(score={a['score']:.2f}, severity={a['severity']})"
            )
        for a in rule_anomalies:
            chain.append(
                f"[A1-Rule] {a['metric'].replace('_',' ').title()} anomaly "
                f"(score={a['score']:.2f})"
            )

    if len(relevant) > 1:
        secondary = relevant[1]
        chain.append(
            f"[{secondary['frame']}] Secondary signal — {secondary['lens'].replace('_',' ').title()}"
            f" relevance {secondary['relevance']:.0%}"
        )

    # Predictor signal (P1/P2): surface risk/ETA even if IF/MiroFish are quiet
    matched_fingerprint = (prediction or {}).get("matched_fingerprint")
    if prediction and prediction.get("risk_level") in ("high", "critical"):
        eta = prediction.get("estimated_incident_in") or "timing unknown"
        chain.append(f"[Predictor] {prediction['risk_level']} risk — {eta}")
    if matched_fingerprint:
        chain.append(f"[Predictor] Matched failure fingerprint: {matched_fingerprint}")

    # P9: reconcile MiroFish top frame with predictor fingerprint's related frame
    if matched_fingerprint and top:
        related_frame = _FINGERPRINT_FRAME.get(matched_fingerprint)
        if related_frame and related_frame != top["frame"]:
            chain.append(
                f"⚠ Predictor fingerprint suggests {related_frame} but MiroFish top frame "
                f"is {top['frame']} — evidence conflict, review manually"
            )

    if not chain:
        chain.append(f"Health degraded to {health_score:.0f}/100 — no dominant signal identified")

    # Confidence: MiroFish relevance + anomaly strength + predictor self-confidence
    top_relevance = top["relevance"] if top else 0.0
    predictor_conf = (prediction or {}).get("self_confidence", 0.0)
    confidence = min(1.0, top_relevance * 0.45 + max_anomaly_score * 0.30 + predictor_conf * 0.25)

    # Fix steps from top frame playbook
    fix_steps = _FRAME_PLAYBOOK.get(top["frame"], _DEFAULT_PLAYBOOK) if top else _DEFAULT_PLAYBOOK

    chain, confidence, mem_refs, pb_refs = _apply_memory(chain, confidence, memory_hits)

    return SynthesisResult(
        root_cause_chain=chain,
        confidence=round(confidence, 3),
        fix_steps=fix_steps,
        method="rule",
        top_frame=top["frame"] if top else None,
        top_frame_lens=top["lens"] if top else None,
        anomaly_methods=anomaly_methods,
        memory_refs=mem_refs,
        memory_influenced=bool(mem_refs),
        playbook_refs=pb_refs,
        playbook_influenced=bool(pb_refs),
    )


def _apply_memory(chain: list[str], base_confidence: float, memory_hits: list):
    """Fold recalled cases into a rule-based chain and adjust confidence.

    Only a *verified* past analysis may move confidence. Two things are
    deliberately excluded:

    - **unverified analyses**, because they are this system's own past guesses.
      Letting them raise confidence closes a loop — "I concluded this before, so
      I'm surer I'm right" — with no new evidence anywhere in it.
    - **playbooks**, because shipped documentation is not evidence that anything
      happened on this host.

    Both still appear in the chain as context; they just don't move the number.
    """
    mem_refs: list[str] = []
    pb_refs: list[str] = []
    confidence = base_confidence

    for hit in memory_hits:
        if getattr(hit, "kind", "analysis") == "playbook":
            pb_refs.append(hit.point_id)
            chain.append(
                f"[Playbook] {hit.title or 'known issue'} — ตรงกัน {hit.similarity:.0%} "
                f"(ความรู้ทั่วไปของ engine ยังไม่ยืนยันว่าเกิดกับ host นี้)"
            )
            continue

        mem_refs.append(hit.point_id)
        mark = "✓ ยืนยันแล้ว" if hit.verified else "ยังไม่ยืนยัน"
        chain.append(
            f"[Memory] เคยเจออาการคล้ายกันเมื่อ {hit.days_ago} วันก่อน · "
            f"ตรงกัน {hit.similarity:.0%} · {mark} · เจอซ้ำ {hit.occurrence_count} ครั้ง"
        )
        if hit.verified and hit.actual_fix:
            chain.append(f"[Memory] ครั้งนั้นแก้ได้จริงด้วย: {hit.actual_fix}")

    verified = [h for h in memory_hits
                if getattr(h, "kind", "analysis") == "analysis" and h.verified]
    if verified:
        top = max(verified, key=lambda h: h.similarity)
        if top.similarity >= MEM_SIM_STRONG:
            confidence = min(0.95, base_confidence + 0.35)
        elif top.similarity >= MEM_SIM_MODERATE:
            confidence = min(0.85, base_confidence + 0.20)

    return chain, confidence, mem_refs, pb_refs


# ─── LLM judge prompt ────────────────────────────────────────────────────────

def _build_judge_prompt(
    host: str,
    health_score: float,
    anomalies: list[dict],
    mirofish_frames: list[dict],
    rule_result: SynthesisResult,
    trend: dict | None = None,
    prediction: dict | None = None,
    perplexica_answer: str | None = None,
    top_errors: list[dict] | None = None,
    propagation_lines: list[str] | None = None,
    knowledge_lines: list[str] | None = None,
    memory_hits: list | None = None,
    forecast_bands: list | None = None,
) -> str:
    relevant_frames = [f for f in mirofish_frames if f["relevance"] > 0]
    frame_lines = []
    for f in relevant_frames:
        frame_lines.append(
            f"  {f['frame']:10s} relevance={f['relevance']:.2f} "
            f"kws=[{', '.join(f['top_keywords'][:3])}]"
        )
        if f.get("insight"):
            frame_lines.append(f"    expert insight: {f['insight']}")
    frames_block = "\n".join(frame_lines)
    errors_block = "\n".join(
        f"  - ({e['count']}x) {e['msg']}" for e in (top_errors or [])
    )
    anomaly_lines = []
    for a in anomalies:
        line = f"  {a['metric']:25s} score={a['score']:.2f} severity={a['severity']}"
        if a.get("current_value") is not None:
            line += f" current={a['current_value']}"
            if a.get("baseline_mean") is not None:
                line += f" baseline_mean={a['baseline_mean']}"
        anomaly_lines.append(line)
    anomaly_block = "\n".join(anomaly_lines)
    rule_chain_block = "\n".join(f"  - {c}" for c in rule_result.root_cause_chain)
    research_block = f"  {perplexica_answer}" if perplexica_answer else "  (no web research)"
    propagation_block = (
        "\n".join(f"  - {l}" for l in propagation_lines)
        if propagation_lines else "  (no topology forecast)"
    )
    knowledge_block = (
        "\n".join(f"  - {l}" for l in knowledge_lines)
        if knowledge_lines else "  (no version knowledge yet)"
    )

    predictor_block = "  (no prediction)"
    if prediction:
        predictor_block = (
            f"  risk_level={prediction.get('risk_level')} "
            f"self_confidence={prediction.get('self_confidence')} "
            f"eta={prediction.get('estimated_incident_in')} "
            f"fingerprint={prediction.get('matched_fingerprint')}"
        )
        if trend:
            predictor_block += f"\n  trend={trend.get('direction')} slope/hr={trend.get('slope_per_hour')}"

    memory_block, playbook_block = _memory_blocks(memory_hits or [])
    forecast_block = _forecast_block(forecast_bands or [])

    return f"""You are an AIOps LLM Judge for a POS (Point-of-Sale) retail system.
Your job is root-cause analysis: trace the observed symptoms back to the single
most likely underlying cause. Distinguish cause from symptom — an error message
is a symptom; the root cause is the condition that produced it (e.g. "gateway
timeout" is a symptom; "WAN link saturation from backup job" is a cause).

Host: {host}
Health score: {health_score:.0f}/100

Top error messages from logs (count x message):
{errors_block or '  (none)'}

Anomaly detectors (A1):
{anomaly_block or '  (none)'}

Multi-frame analysis (A3 MiroFish):
{frames_block or '  (no relevant frames)'}

Predictor (trend+risk):
{predictor_block}

การพยากรณ์เชิงสถิติ (A1b — เทียบกับพฤติกรรมของ host นี้เองในอดีต ณ เวลาเดียวกัน):
{forecast_block}

Rule-based chain (baseline):
{rule_chain_block}

Web research (A2 Perplexica — external context on this failure pattern):
{research_block}

Topology propagation forecast (deterministic simulation on the real
dependency graph — which downstream services this host's condition will
degrade, and when):
{propagation_block}

Known issues for this host's software/OS versions (from accumulated research):
{knowledge_block}

## ประวัติที่ระบบนี้เคยวิเคราะห์ (INTERNAL MEMORY)
{memory_block}

## ความรู้อ้างอิงของ engine นี้ (PLAYBOOK)
{playbook_block}

Reply ONLY with a JSON object, no markdown fences:
{{
  "root_cause_chain": ["<step1: what happened -> why -> downstream effect>", "<step2>", ...],
  "confidence": <float 0.0-1.0>,
  "fix_steps": ["<action1>", "<action2>", ...],
  "reasoning": "<2-4 sentence explanation of how the evidence above points to this root cause, including what would confirm or rule it out>",
  "memory_refs": ["<point_id ของเคสใน INTERNAL MEMORY ที่ใช้จริง>", ...],
  "playbook_refs": ["<id ของ PLAYBOOK ที่ใช้จริง>", ...]
}}

Rules:
- root_cause_chain: 3-5 items, most likely cause first, each item should state the causal
  mechanism (not just a symptom) and be specific to POS context — reference the actual
  errors/anomalies/frames/trend data above rather than generic phrasing
- confidence: how certain you are given the evidence (0=guessing, 1=certain)
- fix_steps: 4-6 concrete operator actions, ordered by priority, each with enough detail to
  act on immediately (what to check/run/restart, not just "investigate X")
- reasoning: connect the dots explicitly between the evidence sections above and your conclusion
- web research is external/general context, not specific telemetry from this host — use it to
  corroborate or add detail to a cause already supported by the evidence above, never as the
  sole basis for root_cause_chain
- If evidence is weak, say so explicitly in root_cause_chain and reflect it in confidence
- **metric ที่ A1b บอกว่า "อยู่ในกรอบ" คือพฤติกรรมปกติของ host นี้ตามเวลานั้น**
  ต่อให้ตัวเลขดูสูงก็ห้ามตีเป็นปัญหา — เช่น error พุ่งทุกตี 2 เพราะ batch job
  คือเรื่องปกติ ไม่ใช่ incident · ให้สนใจตัวที่ "หลุดกรอบ" และยิ่ง breach_magnitude
  สูงยิ่งผิดปกติมาก (วัดเป็นเท่าของความกว้างกรอบ เทียบข้าม metric ได้)

กติกาเรื่อง INTERNAL MEMORY และ PLAYBOOK:
1. ถ้ามีเคสใน INTERNAL MEMORY ที่ "✓ ยืนยันแล้วโดยคน" และตรงกันสูง ให้ยึดเป็นหลัก
   และใส่ point_id ของมันใน memory_refs
2. ถ้าเคสเก่าขัดแย้งกับสัญญาณปัจจุบัน (A1/A3) ให้บอกออกมาตรงๆ ว่าขัดแย้ง อย่ากลบ
   เขียนใน root_cause_chain ว่า "ต่างจากเคส #x ตรงที่..."
3. ห้ามแต่ง point_id หรือ playbook id ที่ไม่ได้อยู่ในรายการข้างบน ถ้าไม่ได้ใช้อันไหน
   ให้ปล่อย list ว่าง
4. ถ้าไม่มีทั้งสองอย่างเลย ให้ทำงานแบบปกติ และปล่อย memory_refs/playbook_refs ว่าง
5. เคสที่ "ยังไม่ยืนยัน" คือคำตอบที่ระบบนี้เดาเองในอดีต ยังไม่มีใครยืนยัน
   ใช้เป็น context ประกอบได้ ห้ามยึดเป็นหลักฐานหลัก
6. PLAYBOOK คือความรู้ทั่วไปของ engine นั้น ไม่ใช่เคสที่เคยเกิดบนเครื่องนี้
   ให้ใช้เป็นสมมติฐานตั้งต้น และต้องเอา "วิธีตรวจสอบ" ของมันไปใส่ใน fix_steps
   ก่อนขั้นตอนที่ลงมือแก้ เพื่อให้คนยืนยันก่อนทำ
7. ถ้า INTERNAL MEMORY (ที่ยืนยันแล้ว) ขัดกับ PLAYBOOK ให้ยึด INTERNAL MEMORY
   แล้วบอกว่าต่างจากตำราตรงไหน"""


def _forecast_block(bands: list) -> str:
    """Render A1b bands.

    Phrased so "in band" reads as a positive statement about normality, not as
    an absence of information — the judge's most useful move here is to *stop*
    treating a large-but-seasonal number as evidence.
    """
    if not bands:
        return "  (ยังไม่มีประวัติพอสำหรับพยากรณ์)"
    lines = []
    for b in bands:
        verdict = (f"หลุดกรอบ (magnitude {b.breach_magnitude:.2f} เท่าของความกว้างกรอบ)"
                   if b.breach else "อยู่ในกรอบ = ปกติสำหรับ host นี้ ณ เวลานี้")
        lines.append(
            f"  {b.metric:14s} คาดว่า {b.p10:.1f}–{b.p90:.1f} (กลาง {b.p50:.1f}) · "
            f"จริง {b.actual:.1f} → {verdict}"
        )
    return "\n".join(lines)


def _memory_blocks(memory_hits: list) -> tuple[str, str]:
    """Render recalled cases as two clearly separated sections.

    Keeping them apart is the point: one is what happened on this host before,
    the other is general documentation. Merged into a single "context" block the
    judge cannot tell which is evidence, and the rules above become unenforceable.
    """
    mem_lines: list[str] = []
    pb_lines: list[str] = []

    for i, hit in enumerate(memory_hits, 1):
        if getattr(hit, "kind", "analysis") == "playbook":
            pb_lines.append(
                f"[KB-{i}] {hit.title} · ตรงกัน {hit.similarity:.2f}\n"
                f"    อาการที่รู้จัก: {hit.symptom_text[:200]}\n"
                f"    สาเหตุที่เป็นไปได้: {'; '.join(hit.root_cause_chain[:3])}\n"
                f"    วิธีตรวจสอบ: {'; '.join(hit.verify_steps[:3])}\n"
                f"    วิธีแก้: {'; '.join(hit.fix_steps[:4])}\n"
                f"    อ้างอิง: {hit.docs_url or '-'}"
            )
            continue

        mark = "✓ ยืนยันแล้วโดยคน" if hit.verified else "ยังไม่ยืนยัน"
        outcome = (
            f"วิธีที่แก้ได้จริง: {hit.actual_fix}"
            if hit.verified and hit.actual_fix
            else f"เสนอให้แก้: {'; '.join(hit.fix_steps[:3])}"
        )
        mem_lines.append(
            f"[{hit.point_id}] เมื่อ {hit.days_ago} วันก่อน · ตรงกัน {hit.similarity:.2f} · "
            f"{mark} · เจอซ้ำ {hit.occurrence_count} ครั้ง\n"
            f"    อาการ: {hit.symptom_text[:300]}\n"
            f"    สรุปตอนนั้น: {'; '.join(hit.root_cause_chain[:3])}\n"
            f"    {outcome}"
        )

    return (
        "\n".join(mem_lines) or "  (ไม่มีประวัติที่ตรงกัน)",
        "\n".join(pb_lines) or "  (ไม่มี playbook ที่ตรงกัน)",
    )


# ─── Main entry points ────────────────────────────────────────────────────────

def synthesize_rule(
    host: str,
    health_score: float,
    anomalies: list[dict],
    mirofish_frames: list[dict],
    trend: dict | None = None,
    prediction: dict | None = None,
    memory_hits: list | None = None,
) -> SynthesisResult:
    """Rule-only pass — instant, no LLM dependency. Its `top_frame` is used to
    build the A2 Perplexica query, so this must run before A2."""
    return _rule_synthesis(host, health_score, anomalies, mirofish_frames,
                           trend, prediction, memory_hits)


async def synthesize(
    host: str,
    health_score: float,
    anomalies: list[dict],
    mirofish_frames: list[dict],
    rule_result: SynthesisResult,
    trend: dict | None = None,
    prediction: dict | None = None,
    perplexica_answer: str | None = None,
    top_errors: list[dict] | None = None,
    propagation_lines: list[str] | None = None,
    knowledge_lines: list[str] | None = None,
    memory_hits: list | None = None,
    forecast_bands: list | None = None,
    ollama_generate=None,
    model: str = "",
    base_url: str = "",
    timeout: float = 30.0,
    temperature: float = 0.1,
    use_llm: bool = False,
) -> SynthesisResult:
    """
    AA Synthesizer LLM judge pass. Takes the already-computed rule_result
    (see synthesize_rule) as baseline/fallback, plus optional A2 web-research
    answer, and asks the LLM to produce a richer root-cause assessment.
    """
    if not (use_llm and ollama_generate and model):
        return rule_result

    prompt = _build_judge_prompt(
        host, health_score, anomalies, mirofish_frames, rule_result, trend, prediction,
        perplexica_answer=perplexica_answer,
        top_errors=top_errors,
        propagation_lines=propagation_lines,
        knowledge_lines=knowledge_lines,
        memory_hits=memory_hits,
        forecast_bands=forecast_bands,
    )
    try:
        raw = await ollama_generate(
            prompt=prompt,
            model=model,
            base_url=base_url,
            timeout=timeout,
            temperature=temperature,
        )
        parsed = json.loads(raw.strip())

        # Keep only ids we actually offered. A small model will happily invent a
        # plausible-looking point_id, and a fabricated citation is worse than no
        # citation — it makes an unsupported claim look sourced.
        offered_mem = {h.point_id for h in (memory_hits or [])
                       if getattr(h, "kind", "analysis") == "analysis"}
        offered_pb = {h.point_id for h in (memory_hits or [])
                      if getattr(h, "kind", "analysis") == "playbook"}
        mem_refs = [r for r in (parsed.get("memory_refs") or []) if r in offered_mem]
        pb_refs = [r for r in (parsed.get("playbook_refs") or []) if r in offered_pb]
        dropped = (len(parsed.get("memory_refs") or []) - len(mem_refs)
                   + len(parsed.get("playbook_refs") or []) - len(pb_refs))
        if dropped:
            logger.warning("AA cited %d id(s) that were never offered — dropped (host=%s)",
                           dropped, host)

        # The judge is free to state its own confidence, but evidence-based
        # boosting stays with the same rule everywhere: only a verified past
        # case moves the number, and never past its ceiling.
        confidence = float(parsed.get("confidence", rule_result.confidence))
        cited = [h for h in (memory_hits or []) if h.point_id in set(mem_refs)]
        verified_cited = [h for h in cited if h.verified]
        if verified_cited:
            top = max(verified_cited, key=lambda h: h.similarity)
            if top.similarity >= MEM_SIM_STRONG:
                confidence = min(0.95, max(confidence, rule_result.confidence + 0.35))
            elif top.similarity >= MEM_SIM_MODERATE:
                confidence = min(0.85, max(confidence, rule_result.confidence + 0.20))
        elif cited:
            # The judge said it leaned on recalled cases, but none of them were
            # ever confirmed by a human — they are this system's own earlier
            # guesses. Letting that raise confidence is the circular loop the
            # design specifically rules out: no new evidence entered the system,
            # so the number must not move. Unverified cases stay as context.
            if confidence > rule_result.confidence:
                logger.info(
                    "AA confidence %.2f -> %.2f for %s — cited only unverified memory",
                    confidence, rule_result.confidence, host,
                )
                confidence = rule_result.confidence

        return SynthesisResult(
            root_cause_chain=parsed.get("root_cause_chain", rule_result.root_cause_chain),
            confidence=confidence,
            fix_steps=parsed.get("fix_steps", rule_result.fix_steps),
            method="llm",
            top_frame=rule_result.top_frame,
            top_frame_lens=rule_result.top_frame_lens,
            anomaly_methods=rule_result.anomaly_methods,
            reasoning=parsed.get("reasoning"),
            memory_refs=mem_refs,
            memory_influenced=bool(mem_refs),
            playbook_refs=pb_refs,
            playbook_influenced=bool(pb_refs),
        )
    except Exception as exc:
        logger.warning("AA Synthesizer LLM failed for %s: %s — falling back to rule", host, exc)
        return rule_result
