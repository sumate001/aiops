"""
POST /ingest — รับ GodEyes native log format แล้วแปลงก่อนส่งต่อไปยัง analyze pipeline

รองรับสองรูปแบบ:
  1. JSON body: {"entries": [...], "tenant_id": "...", ...}
  2. JSONL body (Content-Type: application/x-ndjson): หนึ่ง JSON object ต่อบรรทัด
"""

import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app.config import config
from app.models.ingest import GodEyesIngestRequest
from app.models.request import AnalyzeRequest
from app.models.response import AnalyzeResponse
from app.routers.analyze import analyze
from app.services.godeyes_adapter import build_analyze_request
from app.services import webhook

router = APIRouter()
logger = logging.getLogger(__name__)


async def _run_analyze(analyze_dict: dict) -> AnalyzeResponse:
    req = AnalyzeRequest.model_validate(analyze_dict)
    return await analyze(req)


async def _analyze_and_callback(analyze_dict: dict, callback_url: str) -> None:
    """Background path: run the (slow, A2-heavy) pipeline then POST the result
    to the callback. Keeps /ingest from blocking the caller for minutes."""
    try:
        result = await _run_analyze(analyze_dict)
        await webhook.send(callback_url, result.model_dump(mode="json"))
    except Exception:
        logger.exception("Background analyze/callback failed")


@router.post("/ingest", response_model=AnalyzeResponse)
async def ingest(request: Request) -> AnalyzeResponse:
    content_type = request.headers.get("content-type", "")

    raw_entries: list[dict] = []

    if "ndjson" in content_type or "x-ndjson" in content_type:
        # JSONL stream — one JSON object per line
        body = await request.body()
        for line in body.decode("utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                raw_entries.append(json.loads(line))
            except json.JSONDecodeError as exc:
                logger.warning("Skipping malformed JSONL line: %s", exc)

        request_id = None
        tenant_id = "internal"
        window_from = window_to = None

    else:
        # Standard JSON body: GodEyesIngestRequest
        body = await request.body()
        try:
            payload = GodEyesIngestRequest.model_validate(json.loads(body))
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc))

        raw_entries = payload.entries
        request_id = payload.request_id
        tenant_id = payload.tenant_id or "internal"
        window_from = payload.window_from
        window_to = payload.window_to
        callback_url = payload.callback_url or config.godeye.callback_url

    if "ndjson" in content_type or "x-ndjson" in content_type:
        callback_url = config.godeye.callback_url

    if not raw_entries:
        raise HTTPException(status_code=400, detail={"error": "no entries provided"})

    try:
        analyze_dict = build_analyze_request(
            raw_entries=raw_entries,
            request_id=request_id,
            tenant_id=tenant_id,
            window_from=window_from,
            window_to=window_to,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": str(exc)})

    logger.info(
        "Ingesting %d log entries + %d metrics (from %d raw) tenant=%s",
        len(analyze_dict["entries"]),
        len(analyze_dict.get("metrics", [])),
        len(raw_entries),
        tenant_id,
    )

    # ── Async path: when a callback is configured, run the pipeline in the
    # background and return 202 immediately. The analysis (esp. A2 Perplexica
    # on CPU-bound Ollama) can take several minutes; blocking here would make
    # the caller time out (e.g. logsim streaming reported "0 lines sent").
    # The result is delivered via the callback when ready.
    if callback_url and config.godeye.enabled:
        asyncio.create_task(_analyze_and_callback(analyze_dict, callback_url))
        return JSONResponse(
            status_code=202,
            content={
                "status": "accepted",
                "request_id": request_id,
                "entries": len(analyze_dict["entries"]),
                "metrics": len(analyze_dict.get("metrics", [])),
                "callback_url": callback_url,
            },
        )

    # ── Sync path: no callback → caller wants the result inline. ──
    return await _run_analyze(analyze_dict)
