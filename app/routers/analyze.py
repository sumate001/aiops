import asyncio
import logging
from datetime import datetime, timezone
from dataclasses import dataclass, field

from fastapi import APIRouter, HTTPException

from app.config import config, OLLAMA_TIMEOUT, LLM_TIMEOUT, AIOPS_ML_TIMEOUT, LOG_ML_TIMEOUT, PERPLEXICA_TIMEOUT
from app.models.request import AnalyzeRequest, LogEntry
from app.models.response import (
    AnalyzeResponse,
    AnomalyScore,
    Explanation,
    HostAnalysis,
    MiroFishFrame,
    PerplexicaEnrichment,
    PerplexicaSource,
    Sources,
    Synthesis,
    TopError,
)
from app.services import aiops_ml as ml_client
from app.services import llm as llm_client
from app.services import log_ml_client
from app.services import mirofish
from app.services import synthesizer
from app.services import perplexica_client
from app.services import metrics
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
    filter_entries,
    group_by_host,
    score_to_status,
)

router = APIRouter()
logger = logging.getLogger(__name__)


# ── Intermediate state between phases ──────────────────────────────────────
@dataclass
class _HostState:
    hostname: str
    entries: list[LogEntry]
    window_from: str
    window_to: str
    predict_result: dict | None

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
    synth_result: synthesizer.SynthesisResult | None = None

    # A2 outputs
    enrichment: PerplexicaEnrichment | None = None

    # trend/prediction
    trend: str = "stable"
    prediction: dict | None = None


# ── Phase 1: A1 — Rule-based scoring + IF (no LLM, run all hosts in parallel) ──
async def _phase1_a1(st: _HostState) -> None:
    entries = st.entries
    st.service_profile = next((e.service_profile for e in entries if e.service_profile), None)
    st.criticality = next((e.criticality for e in entries if e.criticality), None)

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

    # health score (before IF)
    anomaly_scores = [a.score for a in st.anomalies]
    st.health_score = compute_host_health_score(st.error_count, st.warn_count, len(entries), anomaly_scores)
    st.status = score_to_status(st.health_score)

    # Persist window stats
    all_msgs = [e.msg for e in entries if e.msg]
    st.sig = extract_signals_from_messages(all_msgs)
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
                st.error_count, st.warn_count, len(entries), anomaly_scores
            )

    # Trend + prediction (no LLM)
    st.trend = analyze_trend(st.hostname)
    st.prediction = generate_prediction(
        host=st.hostname,
        current_health=st.health_score,
        trend=st.trend,
        error_count=st.error_count,
        warn_count=st.warn_count,
        entry_count=len(entries),
        top_error_msgs=st.top_error_msgs,
    )

    logger.info("A1 done — host=%s health=%.1f anomalies=%d",
                st.hostname, st.health_score, len(st.anomalies))


# ── Phase 2: A3 — MiroFish 5-frame (no LLM, run all hosts in parallel) ──
async def _phase2_a3(st: _HostState) -> None:
    st.mirofish_frames = await mirofish.analyze(
        host=st.hostname,
        health_score=st.health_score,
        signal_counts=st.sig,
        top_error_msgs=st.top_error_msgs,
        use_llm=config.llm.enabled,
        ollama_generate=llm_client.generate,
        model=config.llm.model,
        base_url=config.llm.base_url,
        timeout=LLM_TIMEOUT,
        temperature=config.llm.temperature,
    )
    logger.info("A3 done — host=%s frames=%d", st.hostname, len(st.mirofish_frames))


# ── Phase 3: AA Synthesizer (fast, rule-based, run all hosts in parallel) ──
async def _phase3_aa(st: _HostState) -> None:
    st.synth_result = await synthesizer.synthesize(
        host=st.hostname,
        health_score=st.health_score,
        anomalies=[a.model_dump() for a in st.anomalies],
        mirofish_frames=st.mirofish_frames,
        use_llm=config.llm.enabled,
        ollama_generate=llm_client.generate,
        model=config.llm.model,
        base_url=config.llm.base_url,
        timeout=LLM_TIMEOUT,
        temperature=config.llm.temperature,
    )
    logger.info("AA done — host=%s top_frame=%s confidence=%.2f",
                st.hostname, st.synth_result.top_frame, st.synth_result.confidence)


# ── Phase 4: A2 — Perplexica (slow LLM, run one host at a time) ──
async def _phase4_a2(st: _HostState) -> None:
    if not config.perplexica.enabled:
        return
    if not (st.anomalies or any(f["relevance"] > 0 for f in st.mirofish_frames)):
        return

    top_kws = st.mirofish_frames[0]["top_keywords"] if st.mirofish_frames else []
    query = perplexica_client.build_query(
        top_frame=st.synth_result.top_frame if st.synth_result else None,
        top_keywords=top_kws,
        top_error_msgs=st.top_error_msgs,
        host=st.hostname,
    )
    logger.info("A2 start — host=%s query=%s", st.hostname, query[:80])

    perp_result = await perplexica_client.search(
        query=query,
        base_url=config.perplexica.base_url,
        chat_model=config.perplexica.chat_model,
        embedding_model=config.perplexica.embedding_model,
        timeout=PERPLEXICA_TIMEOUT,
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
    ) if sr else Synthesis(
        root_cause_chain=[], confidence=0.0, fix_steps=[], method="rule",
        top_frame=None, top_frame_lens=None, anomaly_methods=[],
    )

    # "ollama_used" really means "the LLM judge ran" — true when the synthesizer
    # produced an LLM-method result (works for any provider, not just Ollama).
    st.ollama_used = bool(sr and sr.method == "llm")

    return HostAnalysis(
        host=st.hostname,
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

    entries = filter_entries(req.entries)
    host_groups = group_by_host(entries)

    window_from = req.window.from_.isoformat().replace("+00:00", "Z")
    window_to = req.window.to.isoformat().replace("+00:00", "Z")

    # aiops-ml predict (disabled by default)
    predict_results: dict[str, dict | None] = {h: None for h in host_groups}
    aiops_ml_used = False
    if config.aiops_ml.enabled:
        async def _predict_host(hostname: str) -> tuple[str, dict | None]:
            host_entries = host_groups[hostname]
            profile = next((e.service_profile for e in host_entries if e.service_profile), None)
            if profile and profile in KNOWN_PROFILES:
                result = await ml_client.predict(
                    hostnames=[hostname], window="2h", horizon="30m",
                    base_url=config.aiops_ml.base_url, timeout=AIOPS_ML_TIMEOUT,
                )
                return hostname, result
            return hostname, None
        pairs = await asyncio.gather(*[_predict_host(h) for h in host_groups])
        for hostname, result in pairs:
            predict_results[hostname] = result
            if result:
                aiops_ml_used = True

    # Build state objects
    states = [
        _HostState(
            hostname=h,
            entries=host_groups[h],
            window_from=window_from,
            window_to=window_to,
            predict_result=predict_results[h],
        )
        for h in host_groups
    ]

    # ── Phase 1: A1 — all hosts in parallel (fast) ──
    logger.info("=== Phase 1: A1 Rule+IF — %d hosts ===", len(states))
    await asyncio.gather(*[_phase1_a1(st) for st in states])

    # ── Phase 2: A3 MiroFish — all hosts in parallel (fast) ──
    logger.info("=== Phase 2: A3 MiroFish — %d hosts ===", len(states))
    await asyncio.gather(*[_phase2_a3(st) for st in states])

    # ── Phase 3: AA Synthesizer — all hosts in parallel (fast) ──
    logger.info("=== Phase 3: AA Synthesizer — %d hosts ===", len(states))
    await asyncio.gather(*[_phase3_aa(st) for st in states])

    # ── Phase 4: A2 Perplexica — one host at a time (slow LLM) ──
    logger.info("=== Phase 4: A2 Perplexica — sequential ===")
    for st in states:
        await _phase4_a2(st)

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
    )

    save_result(response.model_dump(mode="json"))
    return response
