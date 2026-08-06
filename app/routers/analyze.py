import asyncio
import logging
from functools import partial
from datetime import datetime, timezone
from dataclasses import dataclass, field

from fastapi import APIRouter, HTTPException

from app.config import config, OLLAMA_TIMEOUT, LLM_TIMEOUT, AIOPS_ML_TIMEOUT, LOG_ML_TIMEOUT, PERPLEXICA_TIMEOUT
from app.models.request import AnalyzeRequest, LogEntry, MetricSample
from app.models.response import (
    AnalyzeResponse,
    AnomalyScore,
    Explanation,
    HostAnalysis,
    MiroFishFrame,
    PerplexicaEnrichment,
    PerplexicaSource,
    PredictionInfo,
    PropagationForecast,
    PropagationIncident,
    Sources,
    Synthesis,
    TopError,
    TrendInfo,
)
from app.services import aiops_ml as ml_client
from app.services import llm as llm_client
from app.services import log_ml_client
from app.services import mirofish
from app.services import memory_store
from app.services import normalize
from app.services import service_detector
from app.services import synthesizer
from app.services import perplexica_client
from app.services import metrics
from app.services import metric_analyzer
from app.services import knowledge_store
from app.services import propagation
from app.services import topology_store
from app.services.result_store import save_result
from app.services.aiops_ml import KNOWN_PROFILES
from app.knowledge.pos import extract_signals_from_messages
from app.services.baseline_store import WindowStat, save_window_stat
from app.services.predictor import analyze_trend, generate_prediction
from app.services.log_processor import (
    build_summary,
    compute_host_health_score,
    compute_overall_health_score,
    compute_top_errors,
    escalate_status,
    filter_entries,
    group_by_host,
    score_to_status,
    worse_status,
)

router = APIRouter()
logger = logging.getLogger(__name__)


# ── Intermediate state between phases ──────────────────────────────────────
@dataclass
class _HostState:
    hostname: str
    entries: list[LogEntry]
    metric_samples: list[MetricSample]
    window_from: str
    window_to: str
    predict_result: dict | None
    # Count of ALL log entries for this host before the severity filter — the
    # health-score denominator. Using the filtered count (warn+ only) makes the
    # error/warn ratio 100% by construction and pins health at 0.
    total_entry_count: int = 0

    # A1 outputs
    service_profile: str | None = None
    criticality: str | None = None
    error_count: int = 0
    warn_count: int = 0
    health_score: float = 100.0
    status: str = "ok"
    anomalies: list[AnomalyScore] = field(default_factory=list)
    top_errors: list[TopError] = field(default_factory=list)
    top_error_msgs: list[str] = field(default_factory=list)
    sig: dict = field(default_factory=dict)
    explanation: Explanation | None = None
    ollama_used: bool = False

    # A3 outputs
    mirofish_frames: list[dict] = field(default_factory=list)

    # AA outputs
    rule_result: synthesizer.SynthesisResult | None = None  # rule-only pass (drives A2 query)
    synth_result: synthesizer.SynthesisResult | None = None  # final (LLM judge if enabled)

    # engine inferred from the log text (mysql | postgresql | mongodb) — A4 filter
    detected_service: str | None = None

    # A4 memory
    tenant_id: str = "internal"
    symptom_text: str = ""
    memory_hits: list = field(default_factory=list)

    # A2 outputs
    enrichment: PerplexicaEnrichment | None = None

    # trend/prediction — set by _phase1_a1, which always runs before the judge
    trend: TrendInfo | None = None
    prediction: PredictionInfo | None = None

    # Phase 3 topology outputs (หลักฐานเพิ่มให้ judge)
    propagation_lines: list[str] = field(default_factory=list)
    knowledge_lines: list[str] = field(default_factory=list)


# ── Phase 1: A1 — Rule-based scoring + IF (no LLM, run all hosts in parallel) ──
async def _phase1_a1(st: _HostState) -> None:
    entries = st.entries
    st.service_profile = (
        next((e.service_profile for e in entries if e.service_profile), None)
        or next((m.service_profile for m in st.metric_samples if m.service_profile), None)
    )
    st.criticality = (
        next((e.criticality for e in entries if e.criticality), None)
        or next((m.criticality for m in st.metric_samples if m.criticality), None)
    )

    # Which DB engine is this? A4 uses it to filter memory/playbook hits.
    # Trust the ingest label when it names an engine we know; sniff the log text
    # otherwise, and accept None rather than a wrong guess.
    if config.service_detection.enabled:
        st.detected_service, svc_conf = service_detector.resolve_service(
            next((e.service for e in entries if e.service), None),
            [e.msg for e in entries if e.msg],
            sample=config.service_detection.sample_lines,
            min_confidence=config.service_detection.min_confidence,
        )
        logger.info("service detect — host=%s service=%s confidence=%.2f",
                    st.hostname, st.detected_service or "unknown", svc_conf)

    st.error_count = sum(1 for e in entries if e.severity_number >= 17)
    st.warn_count = sum(1 for e in entries if 13 <= e.severity_number <= 16)
    st.top_errors = compute_top_errors(entries)
    st.top_error_msgs = [e.msg for e in st.top_errors]

    # Parse aiops-ml anomalies if any
    if st.predict_result:
        for a in st.predict_result.get("anomalies", []):
            try:
                st.anomalies.append(AnomalyScore(**a))
            except Exception:
                logger.warning("Could not parse anomaly: %s", a)

    # Metric threshold anomalies (GodEye numeric metrics → AnomalyScore)
    if st.metric_samples:
        st.anomalies.extend(metric_analyzer.evaluate_host(st.metric_samples))

    # health score (before IF) — denominator is the pre-filter entry total
    denom = max(st.total_entry_count, len(entries))
    anomaly_scores = [a.score for a in st.anomalies]
    st.health_score = compute_host_health_score(st.error_count, st.warn_count, denom, anomaly_scores)
    st.status = score_to_status(st.health_score)

    all_msgs = [e.msg for e in entries if e.msg]
    st.sig = extract_signals_from_messages(all_msgs)

    # log-ml Isolation Forest score
    if config.log_ml.enabled:
        if_result = await log_ml_client.score_window(
            host=st.hostname,
            tenant_id="internal",
            window_from=st.window_from,
            window_to=st.window_to,
            entry_count=len(entries),
            error_count=st.error_count,
            warn_count=st.warn_count,
            health_score=st.health_score,
            crash_count=st.sig.get("crash", 0),
            auth_fail_count=st.sig.get("auth_fail", 0),
            payment_fail_count=st.sig.get("payment_fail", 0),
            network_err_count=st.sig.get("network_err", 0),
            db_err_count=st.sig.get("db_err", 0),
            hardware_err_count=st.sig.get("hardware_err", 0),
            app_crash_count=st.sig.get("app_crash", 0),
            base_url=config.log_ml.base_url,
            timeout=LOG_ML_TIMEOUT,
        )
        if if_result and if_result["is_anomaly"]:
            raw = if_result["anomaly_score"]
            severity = "high" if raw < -0.3 else "medium"
            st.anomalies.append(AnomalyScore(
                metric="isolation_forest",
                score=round(min(1.0, abs(raw) * 2), 3),
                severity=severity,
            ))
            # recompute with IF included
            anomaly_scores = [a.score for a in st.anomalies]
            st.health_score = compute_host_health_score(
                st.error_count, st.warn_count, denom, anomaly_scores
            )

    # Final status: score-based, then floored by worst anomaly severity so a
    # breached threshold (metric or IF) cannot be diluted down to "ok".
    st.status = escalate_status(score_to_status(st.health_score), st.anomalies)

    # Persist window stats — after IF adjustment so health_score reflects the
    # final post-IF value, not the pre-IF estimate.
    save_window_stat(WindowStat(
        host=st.hostname,
        tenant_id="internal",
        window_from=st.window_from,
        window_to=st.window_to,
        entry_count=len(entries),
        error_count=st.error_count,
        warn_count=st.warn_count,
        health_score=st.health_score,
        top_error_msgs=st.top_error_msgs,
        crash_count=st.sig.get("crash", 0),
        auth_fail_count=st.sig.get("auth_fail", 0),
        payment_fail_count=st.sig.get("payment_fail", 0),
        network_err_count=st.sig.get("network_err", 0),
        db_err_count=st.sig.get("db_err", 0),
        hardware_err_count=st.sig.get("hardware_err", 0),
        app_crash_count=st.sig.get("app_crash", 0),
    ))

    # Trend + prediction (no LLM)
    st.trend = analyze_trend(st.hostname)
    st.prediction = generate_prediction(
        host=st.hostname,
        current_health=st.health_score,
        trend=st.trend,
        error_count=st.error_count,
        warn_count=st.warn_count,
        entry_count=len(entries),
        anomalies=[a.model_dump() for a in st.anomalies],
        top_error_msgs=st.top_error_msgs,
    )

    logger.info("A1 done — host=%s health=%.1f anomalies=%d",
                st.hostname, st.health_score, len(st.anomalies))


# ── Phase 2: A3 — MiroFish 5-frame (no LLM, run all hosts in parallel) ──
async def _phase2_a3(st: _HostState) -> None:
    mf = config.llm.resolve("mirofish")
    st.mirofish_frames = await mirofish.analyze(
        host=st.hostname,
        health_score=st.health_score,
        signal_counts=st.sig,
        top_error_msgs=st.top_error_msgs,
        use_llm=config.llm.enabled and config.llm.mirofish.enabled,
        ollama_generate=partial(llm_client.generate, provider=mf.provider, api_key=mf.api_key),
        model=mf.model,
        base_url=mf.base_url,
        timeout=LLM_TIMEOUT,
        temperature=config.llm.temperature,
    )
    logger.info("A3 done — host=%s frames=%d", st.hostname, len(st.mirofish_frames))


# ── Phase 3: AA rule pass (fast, no LLM, run all hosts in parallel) ──
# Runs before A2 because its `top_frame` drives the A2 search query, and the
# final LLM judge (phase 5) needs A2's answer available as evidence.
async def _phase3_aa_rule(st: _HostState) -> None:
    st.rule_result = synthesizer.synthesize_rule(
        host=st.hostname,
        health_score=st.health_score,
        anomalies=[a.model_dump() for a in st.anomalies],
        mirofish_frames=st.mirofish_frames,
        trend=st.trend.model_dump() if st.trend else None,
        prediction=st.prediction.model_dump() if st.prediction else None,
        memory_hits=st.memory_hits,
    )
    logger.info("AA rule done — host=%s top_frame=%s", st.hostname, st.rule_result.top_frame)


def _health_slope_per_min(hostname: str) -> float:
    """slope ของ health_score (แต้ม/นาที) จาก recent windows — ติดลบ = เสื่อมลง
    ใช้ recency-weighted least squares ตัวเดียวกับ predictor เพื่อความสอดคล้อง"""
    from app.services.baseline_store import get_recent_windows
    from app.services.predictor import _ts, _weighted_slope
    windows = get_recent_windows(hostname, limit=15)
    if len(windows) < 3:
        return 0.0
    windows = sorted(windows, key=lambda w: w["window_from"])
    t0 = _ts(windows[0]["window_from"])
    xs = [(_ts(w["window_from"]) - t0) / 60.0 for w in windows]  # นาที
    ys = [w["health_score"] for w in windows]
    return _weighted_slope(xs, ys)


# ── Phase 3.5: Topology propagation (deterministic, instant) ──────────────
def _run_propagation(tenant_id: str, states: list["_HostState"]) -> PropagationForecast | None:
    """simulate การลามบน dependency graph จาก health จริงของรอบนี้
    แล้วแนบผลเป็นหลักฐานให้ judge (phase 5) + forecast ใน response
    ไม่มี topology หรือไม่มี host match → ข้ามเงียบๆ (degrade gracefully)"""
    try:
        hmap = topology_store.host_map(tenant_id)
        if not hmap and tenant_id != "internal":
            # deployment เดี่ยว: topology มัก upload ใต้ tenant default
            tenant_id = "internal"
            hmap = topology_store.host_map(tenant_id)
        if not hmap:
            return None
        seeds: dict[str, float] = {}
        node_by_host: dict[str, str] = {}
        for st in states:
            node_id = hmap.get(st.hostname)
            if node_id:
                # host หลายตัวอาจ map ลง node เดียว — ใช้ค่าแย่สุด
                seeds[node_id] = min(seeds.get(node_id, 100.0), st.health_score)
                node_by_host[st.hostname] = node_id
        if not seeds:
            return None

        # health-slope ต่อ seed (แต้ม/นาที, ติดลบ = กำลังเสื่อม) จากประวัติ window จริง
        # → seed ที่ "ยังไม่ critical แต่กำลังดิ่ง" จะถูกพยากรณ์ล่วงหน้า
        trends: dict[str, float] = {}
        for st in states:
            node_id = node_by_host.get(st.hostname)
            if not node_id:
                continue
            slope = _health_slope_per_min(st.hostname)
            if slope < 0:
                # หลาย host → node เดียว: ใช้ slope ที่ดิ่งแรงสุด
                trends[node_id] = min(trends.get(node_id, 0.0), slope)

        topo = topology_store.get_topology(tenant_id)
        forecast = propagation.simulate(seeds, topo["edges"], trends=trends)
        labels = topology_store.node_labels(tenant_id)
        lines = propagation.describe(forecast, labels)

        # แจกหลักฐานให้ host ที่อยู่ใน chain ของ incident นั้นๆ
        for st in states:
            node_id = node_by_host.get(st.hostname)
            if not node_id:
                continue
            st.propagation_lines = [
                line for inc, line in zip(forecast["incidents"], lines)
                if node_id in inc["caused_by"]
            ]
            # ความรู้ประจำ version ของ node นี้ (Phase 2) — เท่าที่สะสมได้
            profile = topology_store.get_node(tenant_id, node_id)
            if profile:
                for k in knowledge_store.get_for_node(profile):
                    st.knowledge_lines.append(f"[{k['key']}] {k['answer'][:400]}")
                # host กำลังมีปัญหา → ดัน research profile ขึ้นหัวคิว
                if st.health_score < 70:
                    for sw in profile.get("software") or []:
                        knowledge_store.bump_priority(sw["name"], sw.get("version"))

        if forecast["incidents"]:
            logger.info("Propagation: %d downstream incidents predicted (seeds=%d)",
                        len(forecast["incidents"]), len(seeds))
        return PropagationForecast(
            engine=forecast["engine"],
            horizon_minutes=forecast["horizon_minutes"],
            seeded=forecast["seeded"],
            incidents=[
                PropagationIncident(**inc, label=labels.get(inc["node_id"]))
                for inc in forecast["incidents"]
            ],
        )
    except Exception:
        logger.exception("Propagation step failed — continuing without forecast")
        return None


# ── Phase 2.5: A4 — memory recall (Qdrant) ──
# Runs after A3 rather than beside it, as the plan sketched: symptom_text is
# built from the frames A3 produces, and a query without them retrieves
# noticeably worse. The cost is one extra sequential stage of a few hundred ms.
async def _phase2_5_a4(st: _HostState) -> None:
    if not config.memory.enabled:
        return

    st.symptom_text = normalize.build_symptom_text(
        host=st.hostname,
        service=st.detected_service,
        status=st.status,
        health_score=st.health_score,
        top_keywords=[k for f in st.mirofish_frames for k in f.get("top_keywords", [])],
        frames=st.mirofish_frames,
        anomaly_score=max((a.score for a in st.anomalies), default=None),
        error_msgs=st.top_error_msgs,
    )
    if not st.symptom_text:
        return

    try:
        store = memory_store.get_store()
        # Memory is an enhancement, never a dependency — a slow or dead Qdrant
        # must cost us the recall, not the analysis.
        st.memory_hits = await asyncio.wait_for(
            asyncio.to_thread(
                store.search,
                st.symptom_text,
                st.tenant_id,
                service=st.detected_service,
                limit=config.memory.top_k,
                prefer_verified=config.memory.prefer_verified,
            ),
            timeout=config.memory.timeout_seconds,
        )
        logger.info("A4 memory — host=%s hits=%d (%s)", st.hostname, len(st.memory_hits),
                    ", ".join(f"{h.kind}:{h.similarity:.2f}" for h in st.memory_hits) or "none")
    except (asyncio.TimeoutError, Exception) as exc:
        logger.warning("A4 memory search failed, continuing without: %s", exc)
        st.memory_hits = []


# ── Phase 4: A2 — Perplexica (slow LLM, run one host at a time) ──
async def _phase4_a2(st: _HostState) -> None:
    if not config.perplexica.enabled:
        return
    if not (st.anomalies or any(f["relevance"] > 0 for f in st.mirofish_frames)):
        return

    top_kws = st.mirofish_frames[0]["top_keywords"] if st.mirofish_frames else []
    query = perplexica_client.build_query(
        top_frame=st.rule_result.top_frame if st.rule_result else None,
        top_keywords=top_kws,
        top_error_msgs=st.top_error_msgs,
        host=st.hostname,
        # detector names (isolation_forest) aren't searchable system metrics
        anomaly_metrics=[a.metric for a in st.anomalies if a.metric != "isolation_forest"],
    )
    if not query:
        logger.info("A2 skip — host=%s (no searchable evidence)", st.hostname)
        return
    logger.info("A2 start — host=%s query=%s", st.hostname, query[:80])

    # Chat provider/model follow the resolved AI-judge stage (global default, or
    # the Perplexica override). Embeddings stay on Perplexica's own Ollama model.
    px = config.llm.resolve("perplexica")
    perp_result = await perplexica_client.search(
        query=query,
        base_url=config.perplexica.base_url,
        chat_model=px.model,
        embedding_model=config.perplexica.embedding_model,
        timeout=PERPLEXICA_TIMEOUT,
        chat_provider=px.provider,
        chat_base_url=px.base_url,
        chat_api_key=px.api_key,
        mode=config.perplexica.mode,
    )
    if perp_result:
        st.enrichment = PerplexicaEnrichment(
            query=perp_result["query"],
            answer=perp_result["answer"],
            sources=[PerplexicaSource(**s) for s in perp_result["sources"]],
        )
        logger.info("A2 OK — host=%s answer_len=%d sources=%d",
                    st.hostname, len(perp_result["answer"]), len(perp_result["sources"]))
    else:
        logger.info("A2 skip — host=%s (no result)", st.hostname)


# ── Phase 5: AA LLM judge (run all hosts in parallel) — sees A2's answer ──
async def _phase5_aa_llm(st: _HostState) -> None:
    sy = config.llm.resolve("synthesizer")
    # Only grounded research reaches the judge. When the web search returns no
    # sources, Perplexica still answers — from its own model's memory, marking
    # the text "[no source]" — and that prose is indistinguishable from real
    # evidence once it's in the prompt. Withhold it rather than launder it.
    research = None
    if st.enrichment:
        if st.enrichment.sources:
            research = st.enrichment.answer
        else:
            logger.info("A2 answer withheld from judge — host=%s (0 sources)", st.hostname)
    st.synth_result = await synthesizer.synthesize(
        host=st.hostname,
        health_score=st.health_score,
        anomalies=[a.model_dump() for a in st.anomalies],
        mirofish_frames=st.mirofish_frames,
        rule_result=st.rule_result,
        trend=st.trend.model_dump() if st.trend else None,
        prediction=st.prediction.model_dump() if st.prediction else None,
        perplexica_answer=research,
        top_errors=[e.model_dump() for e in st.top_errors],
        propagation_lines=st.propagation_lines,
        knowledge_lines=st.knowledge_lines,
        memory_hits=st.memory_hits,
        use_llm=config.llm.enabled,
        ollama_generate=partial(llm_client.generate, provider=sy.provider, api_key=sy.api_key),
        model=sy.model,
        base_url=sy.base_url,
        timeout=LLM_TIMEOUT,
        temperature=config.llm.temperature,
    )
    logger.info("AA done — host=%s top_frame=%s confidence=%.2f",
                st.hostname, st.synth_result.top_frame, st.synth_result.confidence)


# ── Build final HostAnalysis from state ────────────────────────────────────
def _build_host_analysis(st: _HostState) -> tuple[HostAnalysis, bool]:
    sr = st.synth_result
    synthesis = Synthesis(
        root_cause_chain=sr.root_cause_chain,
        confidence=sr.confidence,
        fix_steps=sr.fix_steps,
        method=sr.method,
        top_frame=sr.top_frame,
        top_frame_lens=sr.top_frame_lens,
        anomaly_methods=sr.anomaly_methods,
        reasoning=sr.reasoning,
        memory_refs=sr.memory_refs,
        memory_influenced=sr.memory_influenced,
        playbook_refs=sr.playbook_refs,
        playbook_influenced=sr.playbook_influenced,
    ) if sr else Synthesis(
        root_cause_chain=[], confidence=0.0, fix_steps=[], method="rule",
        top_frame=None, top_frame_lens=None, anomaly_methods=[], reasoning=None,
    )

    # "ollama_used" really means "the LLM judge ran" — true when the synthesizer
    # produced an LLM-method result (works for any provider, not just Ollama).
    st.ollama_used = bool(sr and sr.method == "llm")

    return HostAnalysis(
        host=st.hostname,
        detected_service=st.detected_service,
        memory_hits=st.memory_hits,
        service_profile=st.service_profile,
        criticality=st.criticality,
        entry_count=len(st.entries),
        error_count=st.error_count,
        warn_count=st.warn_count,
        health_score=st.health_score,
        status=st.status,
        anomalies=st.anomalies,
        top_errors=st.top_errors,
        explanation=st.explanation,
        trend=st.trend,
        prediction=st.prediction,
        mirofish=[MiroFishFrame(**f) for f in st.mirofish_frames],
        synthesis=synthesis,
        enrichment=st.enrichment,
    ), st.ollama_used


# ── Main endpoint ───────────────────────────────────────────────────────────
@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze(req: AnalyzeRequest) -> AnalyzeResponse:
    if req.window.from_ >= req.window.to:
        raise HTTPException(status_code=400, detail={"error": "window.from must be before window.to"})

    if not req.entries and not req.metrics:
        raise HTTPException(status_code=400, detail={"error": "no log entries or metrics provided"})

    entries = filter_entries(req.entries)
    host_groups = group_by_host(entries)
    # Pre-filter totals per host (health-score denominator).
    raw_totals: dict[str, int] = {}
    for e in req.entries:
        raw_totals[e.host] = raw_totals.get(e.host, 0) + 1
    metric_groups = metric_analyzer.group_by_host(req.metrics)

    # Hosts may appear in logs, metrics, or both (preserve first-seen order).
    all_hosts = list(dict.fromkeys([*host_groups.keys(), *metric_groups.keys()]))

    window_from = req.window.from_.isoformat().replace("+00:00", "Z")
    window_to = req.window.to.isoformat().replace("+00:00", "Z")

    # aiops-ml predict (disabled by default)
    predict_results: dict[str, dict | None] = {h: None for h in all_hosts}
    aiops_ml_used = False
    if config.aiops_ml.enabled:
        async def _predict_host(hostname: str) -> tuple[str, dict | None]:
            host_entries = host_groups.get(hostname, [])
            profile = next((e.service_profile for e in host_entries if e.service_profile), None)
            if profile and profile in KNOWN_PROFILES:
                result = await ml_client.predict(
                    hostnames=[hostname], window="2h", horizon="30m",
                    base_url=config.aiops_ml.base_url, timeout=AIOPS_ML_TIMEOUT,
                )
                return hostname, result
            return hostname, None
        pairs = await asyncio.gather(*[_predict_host(h) for h in all_hosts])
        for hostname, result in pairs:
            predict_results[hostname] = result
            if result:
                aiops_ml_used = True

    # Build state objects
    states = [
        _HostState(
            hostname=h,
            entries=host_groups.get(h, []),
            metric_samples=metric_groups.get(h, []),
            window_from=window_from,
            window_to=window_to,
            predict_result=predict_results[h],
            total_entry_count=raw_totals.get(h, 0),
            tenant_id=req.tenant_id,
        )
        for h in all_hosts
    ]

    # ── Phase 1: A1 — all hosts in parallel (fast) ──
    logger.info("=== Phase 1: A1 Rule+IF — %d hosts ===", len(states))
    await asyncio.gather(*[_phase1_a1(st) for st in states])

    # ── Phase 2: A3 MiroFish — all hosts in parallel (fast) ──
    logger.info("=== Phase 2: A3 MiroFish — %d hosts ===", len(states))
    await asyncio.gather(*[_phase2_a3(st) for st in states])

    # ── Phase 2.5: A4 memory recall — all hosts in parallel (needs A3's frames) ──
    logger.info("=== Phase 2.5: A4 memory — %d hosts ===", len(states))
    await asyncio.gather(*[_phase2_5_a4(st) for st in states])

    # ── Phase 3: AA rule pass — all hosts in parallel (fast, no LLM) ──
    logger.info("=== Phase 3: AA rule pass — %d hosts ===", len(states))
    await asyncio.gather(*[_phase3_aa_rule(st) for st in states])

    # ── Phase 3.5: Topology propagation — deterministic, instant ──
    prop_forecast = _run_propagation(req.tenant_id, states)

    # ── Phase 4: A2 Perplexica — one host at a time (slow LLM) ──
    logger.info("=== Phase 4: A2 Perplexica — sequential ===")
    for st in states:
        await _phase4_a2(st)

    # ── Phase 5: AA LLM judge — all hosts in parallel, sees A2's answer ──
    logger.info("=== Phase 5: AA LLM judge — %d hosts ===", len(states))
    await asyncio.gather(*[_phase5_aa_llm(st) for st in states])

    # Assemble final response
    host_analyses: list[HostAnalysis] = []
    any_ollama_used = False
    for st in states:
        ha, ollama_used = _build_host_analysis(st)
        host_analyses.append(ha)
        if ollama_used:
            any_ollama_used = True

    overall_score = compute_overall_health_score(host_analyses)
    overall_status = score_to_status(overall_score)
    # Don't let an averaged overall score hide a host that's already critical.
    for ha in host_analyses:
        overall_status = worse_status(overall_status, ha.status)
    summary = build_summary(host_analyses)

    metrics.analyze_requests_total.inc()
    metrics.record_analysis(req.tenant_id, host_analyses, overall_score, overall_status)

    response = AnalyzeResponse(
        request_id=req.request_id,
        tenant_id=req.tenant_id,
        window={"from": window_from, "to": window_to},
        analyzed_at=datetime.now(tz=timezone.utc),
        health_score=overall_score,
        status=overall_status,
        hosts=host_analyses,
        summary=summary,
        sources=Sources(
            aiops_ml_used=aiops_ml_used,
            ollama_used=any_ollama_used,
            ollama_model=config.llm.model if config.llm.enabled else config.ollama.model,
        ),
        propagation_forecast=prop_forecast,
    )

    result_id = save_result(response.model_dump(mode="json"))

    # Index into memory without making the caller wait: embedding + upsert costs
    # a few hundred ms per host and adds nothing to this response. A failure here
    # means we don't remember this window — not that the analysis failed.
    if config.memory.enabled and result_id is not None:
        asyncio.create_task(_persist_to_memory(states, str(result_id)))

    return response


async def _persist_to_memory(states: list[_HostState], result_id: str) -> None:
    """Fire-and-forget write of each host's analysis into A4 memory."""
    try:
        store = memory_store.get_store()
        await asyncio.to_thread(store.ensure_collection)
        for st in states:
            if not st.symptom_text or not st.synth_result:
                continue
            try:
                point_id = await asyncio.to_thread(
                    store.upsert_analysis,
                    st.symptom_text,
                    st.tenant_id,
                    st.hostname,
                    result_id,
                    service=st.detected_service,
                    frame=st.synth_result.top_frame,
                    severity=st.status,
                    root_cause_chain=st.synth_result.root_cause_chain,
                    fix_steps=st.synth_result.fix_steps,
                    confidence=st.synth_result.confidence,
                )
                logger.info("A4 memory stored — host=%s point=%s", st.hostname, point_id)
            except Exception as exc:
                logger.warning("A4 memory upsert failed for %s: %s", st.hostname, exc)
    except Exception as exc:
        logger.warning("A4 memory persist skipped: %s", exc)
