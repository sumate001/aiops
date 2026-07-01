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


def _rule_synthesis(
    host: str,
    health_score: float,
    anomalies: list[dict],
    mirofish_frames: list[dict],
    trend: dict | None = None,
    prediction: dict | None = None,
) -> SynthesisResult:
    """
    Pure-rule synthesis — no LLM, instant.
    Weighs MiroFish relevance + anomaly severity to produce root_cause_chain.
    """
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

    return SynthesisResult(
        root_cause_chain=chain,
        confidence=round(confidence, 3),
        fix_steps=fix_steps,
        method="rule",
        top_frame=top["frame"] if top else None,
        top_frame_lens=top["lens"] if top else None,
        anomaly_methods=anomaly_methods,
    )


# ─── LLM judge prompt ────────────────────────────────────────────────────────

def _build_judge_prompt(
    host: str,
    health_score: float,
    anomalies: list[dict],
    mirofish_frames: list[dict],
    rule_result: SynthesisResult,
    trend: dict | None = None,
    prediction: dict | None = None,
) -> str:
    relevant_frames = [f for f in mirofish_frames if f["relevance"] > 0]
    frames_block = "\n".join(
        f"  {f['frame']:10s} relevance={f['relevance']:.2f} "
        f"kws=[{', '.join(f['top_keywords'][:3])}]"
        for f in relevant_frames
    )
    anomaly_block = "\n".join(
        f"  {a['metric']:25s} score={a['score']:.2f} severity={a['severity']}"
        for a in anomalies
    )
    rule_chain_block = "\n".join(f"  - {c}" for c in rule_result.root_cause_chain)

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

    return f"""You are an AIOps LLM Judge for a POS (Point-of-Sale) retail system.
Analyze the evidence and synthesize a concise root-cause assessment.

Host: {host}
Health score: {health_score:.0f}/100

Anomaly detectors (A1):
{anomaly_block or '  (none)'}

Multi-frame analysis (A3 MiroFish):
{frames_block or '  (no relevant frames)'}

Predictor (trend+risk):
{predictor_block}

Rule-based chain (baseline):
{rule_chain_block}

Reply ONLY with a JSON object, no markdown fences:
{{
  "root_cause_chain": ["<step1>", "<step2>", ...],
  "confidence": <float 0.0-1.0>,
  "fix_steps": ["<action1>", "<action2>", ...]
}}

Rules:
- root_cause_chain: 2-4 items, most likely cause first, be specific to POS context
- confidence: how certain you are given the evidence (0=guessing, 1=certain)
- fix_steps: 3-5 concrete operator actions, ordered by priority
- If evidence is weak, say so explicitly in root_cause_chain"""


# ─── Main entry point ─────────────────────────────────────────────────────────

async def synthesize(
    host: str,
    health_score: float,
    anomalies: list[dict],
    mirofish_frames: list[dict],
    trend: dict | None = None,
    prediction: dict | None = None,
    ollama_generate=None,
    model: str = "",
    base_url: str = "",
    timeout: float = 30.0,
    temperature: float = 0.1,
    use_llm: bool = False,
) -> SynthesisResult:
    """
    AA Synthesizer entry point.
    Always runs rule-based synthesis first (fallback).
    If use_llm=True and ollama_generate is provided, enriches with LLM judge.
    """
    rule_result = _rule_synthesis(host, health_score, anomalies, mirofish_frames, trend, prediction)

    if not (use_llm and ollama_generate and model):
        return rule_result

    prompt = _build_judge_prompt(
        host, health_score, anomalies, mirofish_frames, rule_result, trend, prediction
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
        return SynthesisResult(
            root_cause_chain=parsed.get("root_cause_chain", rule_result.root_cause_chain),
            confidence=float(parsed.get("confidence", rule_result.confidence)),
            fix_steps=parsed.get("fix_steps", rule_result.fix_steps),
            method="llm",
            top_frame=rule_result.top_frame,
            top_frame_lens=rule_result.top_frame_lens,
            anomaly_methods=rule_result.anomaly_methods,
        )
    except Exception as exc:
        logger.warning("AA Synthesizer LLM failed for %s: %s — falling back to rule", host, exc)
        return rule_result
