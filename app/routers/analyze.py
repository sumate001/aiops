import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from app.config import config, OLLAMA_TIMEOUT, AIOPS_ML_TIMEOUT, LOG_ML_TIMEOUT, PERPLEXICA_TIMEOUT
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
from app.services import ollama as ollama_client
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


async def _analyze_host(
    hostname: str,
    entries: list[LogEntry],
    window_from: str,
    window_to: str,
    predict_result: dict | None,
) -> HostAnalysis:
    service_profile = next((e.service_profile for e in entries if e.service_profile), None)
    criticality = next((e.criticality for e in entries if e.criticality), None)

    error_count = sum(1 for e in entries if e.severity_number >= 17)
    warn_count = sum(1 for e in entries if 13 <= e.severity_number <= 16)
    top_errors = compute_top_errors(entries)

    # Parse anomalies from predict result
    anomalies: list[AnomalyScore] = []
    if predict_result:
        raw_anomalies = predict_result.get("anomalies", [])
        for a in raw_anomalies:
            try:
                anomalies.append(AnomalyScore(**a))
            except Exception:
                logger.warning("Could not parse anomaly from predict response: %s", a)

    anomaly_scores = [a.score for a in anomalies]
    health_score = compute_host_health_score(error_count, warn_count, len(entries), anomaly_scores)
    status = score_to_status(health_score)

    # Step 4: Ollama log pattern analysis
    explanation: Explanation | None = None
    ollama_used = False

    if top_errors or anomalies:
        prompt = ollama_client.build_analysis_prompt(
            hostname=hostname,
            service_profile=service_profile,
            criticality=criticality,
            window_from=window_from,
            window_to=window_to,
            top_errors=[{"msg": e.msg, "count": e.count} for e in top_errors],
            anomalies=[{"metric": a.metric, "score": a.score, "severity": a.severity} for a in anomalies],
        )
        try:
            raw_text = await ollama_client.generate(
                prompt=prompt,
                model=config.ollama.model,
                base_url=config.ollama.base_url,
                timeout=OLLAMA_TIMEOUT,
                temperature=config.ollama.temperature,
            )
            parsed = ollama_client.parse_json_response(raw_text)
            explanation = Explanation(**parsed)
            ollama_used = True
        except ollama_client.OllamaError as exc:
            logger.warning("Ollama failed for host %s: %s", hostname, exc)

    # Step 5: aiops-ml /explain for gold tier (overrides Ollama if available)
    if (
        config.aiops_ml.enabled
        and criticality == "gold"
        and anomalies
    ):
        explain_result = await ml_client.explain(
            host=hostname,
            window_from=window_from,
            window_to=window_to,
            anomalies=[a.model_dump() for a in anomalies],
            base_url=config.aiops_ml.base_url,
            timeout=AIOPS_ML_TIMEOUT,
        )
        if explain_result:
            try:
                explanation = Explanation(**explain_result)
            except Exception:
                logger.warning("Could not parse /explain response for %s, using Ollama fallback", hostname)

    # ── Persist window stats (baseline building) ──
    # รวม messages จากทุก entries (ไม่แค่ top_errors) เพื่อ extract signals ครบทุกประเภท
    all_msgs = [e.msg for e in entries if e.msg]
    sig = extract_signals_from_messages(all_msgs)
    top_error_msgs = [e.msg for e in top_errors]

    save_window_stat(WindowStat(
        host=hostname,
        tenant_id="internal",
        window_from=window_from,
        window_to=window_to,
        entry_count=len(entries),
        error_count=error_count,
        warn_count=warn_count,
        health_score=health_score,
        top_error_msgs=top_error_msgs,
        crash_count=sig.get("crash", 0),
        auth_fail_count=sig.get("auth_fail", 0),
        payment_fail_count=sig.get("payment_fail", 0),
        network_err_count=sig.get("network_err", 0),
        db_err_count=sig.get("db_err", 0),
        hardware_err_count=sig.get("hardware_err", 0),
        app_crash_count=sig.get("app_crash", 0),
    ))

    # ── log-ml Isolation Forest score ──
    if config.log_ml.enabled:
        if_result = await log_ml_client.score_window(
            host=hostname,
            tenant_id="internal",
            window_from=window_from,
            window_to=window_to,
            entry_count=len(entries),
            error_count=error_count,
            warn_count=warn_count,
            health_score=health_score,
            crash_count=sig.get("crash", 0),
            auth_fail_count=sig.get("auth_fail", 0),
            payment_fail_count=sig.get("payment_fail", 0),
            network_err_count=sig.get("network_err", 0),
            db_err_count=sig.get("db_err", 0),
            hardware_err_count=sig.get("hardware_err", 0),
            app_crash_count=sig.get("app_crash", 0),
            base_url=config.log_ml.base_url,
            timeout=LOG_ML_TIMEOUT,
        )
        if if_result and if_result["is_anomaly"]:
            raw = if_result["anomaly_score"]   # negative = anomaly, closer to 0 = more anomalous
            severity = "high" if raw < -0.3 else "medium"
            anomalies.append(AnomalyScore(
                metric="isolation_forest",
                score=round(min(1.0, abs(raw) * 2), 3),
                severity=severity,
            ))
            # recompute health_score with IF anomaly included
            anomaly_scores = [a.score for a in anomalies]
            health_score = compute_host_health_score(error_count, warn_count, len(entries), anomaly_scores)

    # ── A3 MiroFish — 5-frame parallel analysis ──
    mirofish_frames = await mirofish.analyze(
        host=hostname,
        health_score=health_score,
        signal_counts=sig,
        top_error_msgs=top_error_msgs,
        use_llm=False,    # LLM enrichment handled by AA Synthesizer
    )
    mirofish_results = [MiroFishFrame(**f) for f in mirofish_frames]

    # ── AA Synthesizer — LLM-as-Judge ──
    synth_result = await synthesizer.synthesize(
        host=hostname,
        health_score=health_score,
        anomalies=[a.model_dump() for a in anomalies],
        mirofish_frames=mirofish_frames,
        use_llm=False,
    )
    synthesis = Synthesis(
        root_cause_chain=synth_result.root_cause_chain,
        confidence=synth_result.confidence,
        fix_steps=synth_result.fix_steps,
        method=synth_result.method,
        top_frame=synth_result.top_frame,
        top_frame_lens=synth_result.top_frame_lens,
        anomaly_methods=synth_result.anomaly_methods,
    )

    # ── A2 Perplexica — external knowledge enrichment ──
    enrichment: PerplexicaEnrichment | None = None
    if config.perplexica.enabled and (anomalies or any(f["relevance"] > 0 for f in mirofish_frames)):
        top_kws = mirofish_frames[0]["top_keywords"] if mirofish_frames else []
        query = perplexica_client.build_query(
            top_frame=synth_result.top_frame,
            top_keywords=top_kws,
            top_error_msgs=top_error_msgs,
            host=hostname,
        )
        perp_result = await perplexica_client.search(
            query=query,
            base_url=config.perplexica.base_url,
            timeout=PERPLEXICA_TIMEOUT,
        )
        if perp_result:
            enrichment = PerplexicaEnrichment(
                query=perp_result["query"],
                answer=perp_result["answer"],
                sources=[PerplexicaSource(**s) for s in perp_result["sources"]],
            )

    # ── Trend analysis + prediction ──
    trend = analyze_trend(hostname)
    prediction = generate_prediction(
        host=hostname,
        current_health=health_score,
        trend=trend,
        error_count=error_count,
        warn_count=warn_count,
        entry_count=len(entries),
        top_error_msgs=top_error_msgs,
    )

    return HostAnalysis(
        host=hostname,
        service_profile=service_profile,
        criticality=criticality,
        entry_count=len(entries),
        error_count=error_count,
        warn_count=warn_count,
        health_score=health_score,
        status=status,
        anomalies=anomalies,
        top_errors=top_errors,
        explanation=explanation,
        trend=trend,
        prediction=prediction,
        mirofish=mirofish_results,
        synthesis=synthesis,
        enrichment=enrichment,
    ), ollama_used


@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze(req: AnalyzeRequest) -> AnalyzeResponse:
    # Validate window ordering
    if req.window.from_ >= req.window.to:
        raise HTTPException(status_code=400, detail={"error": "window.from must be before window.to"})

    # Step 1: filter + cap
    entries = filter_entries(req.entries)

    # Step 2: group by host
    host_groups = group_by_host(entries)

    window_from = req.window.from_.isoformat().replace("+00:00", "Z")
    window_to = req.window.to.isoformat().replace("+00:00", "Z")

    # Step 3: concurrent aiops-ml /predict per host
    predict_results: dict[str, dict | None] = {h: None for h in host_groups}
    aiops_ml_used = False

    if config.aiops_ml.enabled:
        async def _predict_host(hostname: str) -> tuple[str, dict | None]:
            host_entries = host_groups[hostname]
            profile = next((e.service_profile for e in host_entries if e.service_profile), None)
            if profile and profile in KNOWN_PROFILES:
                result = await ml_client.predict(
                    hostnames=[hostname],
                    window="2h",
                    horizon="30m",
                    base_url=config.aiops_ml.base_url,
                    timeout=AIOPS_ML_TIMEOUT,
                )
                return hostname, result
            return hostname, None

        predict_tasks = [_predict_host(h) for h in host_groups]
        predict_pairs = await asyncio.gather(*predict_tasks)
        for hostname, result in predict_pairs:
            predict_results[hostname] = result
            if result is not None:
                aiops_ml_used = True

    # Steps 4-5-6: analyze each host concurrently
    host_tasks = [
        _analyze_host(
            hostname=h,
            entries=host_groups[h],
            window_from=window_from,
            window_to=window_to,
            predict_result=predict_results[h],
        )
        for h in host_groups
    ]
    results = await asyncio.gather(*host_tasks)

    host_analyses: list[HostAnalysis] = []
    any_ollama_used = False
    for ha, ollama_used in results:
        host_analyses.append(ha)
        if ollama_used:
            any_ollama_used = True

    # Step 6: overall health score
    overall_score = compute_overall_health_score(host_analyses)
    overall_status = score_to_status(overall_score)

    # Step 7: assemble response
    summary = build_summary(host_analyses)

    # ── Prometheus metrics ──
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
            ollama_model=config.ollama.model,
        ),
    )

    save_result(response.model_dump(mode="json"))
    return response
