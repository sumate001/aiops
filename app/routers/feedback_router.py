"""Feedback loop — the thing that makes memory worth having.

Without this every stored case is the system's own unconfirmed guess, and
recalling one only repeats it. A human verdict is the only signal that turns a
recalled case into evidence, which is why confidence refuses to move without it.

Feedback is per (result_id, host): one analysis covers several hosts and each
gets its own memory point, so a verdict has to say which host it is about.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import config
from app.services import feedback_store, memory_store, metrics
from app.services.memory_store import KIND_PLAYBOOK

logger = logging.getLogger(__name__)
router = APIRouter()

VALID_VERDICTS = {"correct", "wrong", "partial"}


class FeedbackRequest(BaseModel):
    verdict: str
    actual_root_cause: str | None = None
    actual_fix: str | None = None
    resolved_by: str | None = None
    note: str | None = None


def _store():
    if not config.memory.enabled:
        raise HTTPException(status_code=503, detail={"error": "memory is disabled"})
    return memory_store.get_store()


def _load_point(point_id: str):
    try:
        point = _store().get(point_id)
    except Exception as exc:
        raise HTTPException(status_code=503, detail={"error": f"memory unavailable: {exc}"})
    if not point:
        raise HTTPException(status_code=404, detail={"error": f"point {point_id} not found"})
    return point


@router.post("/api/results/{result_id}/hosts/{host}/feedback")
async def submit_feedback(result_id: str, host: str, body: FeedbackRequest):
    if body.verdict not in VALID_VERDICTS:
        raise HTTPException(
            status_code=400,
            detail={"error": f"verdict must be one of {sorted(VALID_VERDICTS)}"},
        )

    # A "wrong" verdict without the correction is the single most valuable thing
    # a human can tell this system, and also the easiest to lose. Refuse the
    # request rather than record that we were wrong and forget how.
    if body.verdict == "wrong" and not (body.actual_root_cause or body.actual_fix):
        raise HTTPException(
            status_code=400,
            detail={"error": "verdict 'wrong' requires actual_root_cause or actual_fix — "
                             "บอกด้วยว่าที่ถูกคืออะไร ไม่งั้นระบบไม่ได้เรียนรู้อะไรเลย"},
        )

    link = feedback_store.get_memory_point(result_id, host)
    if not link:
        raise HTTPException(
            status_code=404,
            detail={"error": f"no memory point for result {result_id} host {host}"},
        )

    point = _load_point(link["point_id"])
    if (point.payload or {}).get("kind") == KIND_PLAYBOOK:
        # Playbooks are shipped content under __global__: editing one here would
        # change it for every tenant, and the next seed run would overwrite the
        # edit anyway.
        raise HTTPException(
            status_code=409,
            detail={"error": "cannot edit a playbook via feedback — "
                             "แก้ที่ไฟล์ใน app/knowledge/playbooks/ แล้ว re-seed แทน"},
        )

    try:
        _store().mark_verified(link["point_id"], body.model_dump())
    except Exception as exc:
        raise HTTPException(status_code=503, detail={"error": f"memory write failed: {exc}"})

    metrics.feedback_total.labels(verdict=body.verdict).inc()
    logger.info("feedback %s — result=%s host=%s point=%s by=%s",
                body.verdict, result_id, host, link["point_id"], body.resolved_by or "-")
    return {"ok": True, "memory_point_id": link["point_id"]}


@router.get("/api/results/{result_id}/hosts/{host}/feedback")
async def get_feedback(result_id: str, host: str):
    link = feedback_store.get_memory_point(result_id, host)
    if not link:
        raise HTTPException(
            status_code=404,
            detail={"error": f"no memory point for result {result_id} host {host}"},
        )
    payload = (_load_point(link["point_id"]).payload) or {}
    return {
        "memory_point_id": link["point_id"],
        "verified": bool(payload.get("verified")),
        "verdict": payload.get("feedback_verdict"),
        "actual_root_cause": payload.get("actual_root_cause"),
        "actual_fix": payload.get("actual_fix"),
        "resolved_by": payload.get("resolved_by"),
        "verified_at": payload.get("verified_at"),
    }


@router.get("/api/memory/stats")
async def memory_stats(tenant_id: str | None = None):
    store = _store()
    try:
        from qdrant_client import models

        total = store.client.count(store.collection).count
        stats = {"total_points": total, "verified_count": 0,
                 "by_kind": {}, "by_frame": {}, "by_verdict": {}, "top_recurring": []}

        # The collection is capped by prune policy elsewhere; scrolling it is
        # cheap enough for a stats endpoint and avoids maintaining counters.
        records, _ = store.client.scroll(
            store.collection, limit=10000, with_payload=True, with_vectors=False,
            scroll_filter=(
                models.Filter(must=[models.FieldCondition(
                    key="tenant_id", match=models.MatchAny(any=[tenant_id, "__global__"]))])
                if tenant_id else None
            ),
        )
        recurring = []
        for r in records:
            p = r.payload or {}
            kind = p.get("kind", "analysis")
            stats["by_kind"][kind] = stats["by_kind"].get(kind, 0) + 1
            if p.get("verified"):
                stats["verified_count"] += 1
            frame = p.get("frame") or "unknown"
            stats["by_frame"][frame] = stats["by_frame"].get(frame, 0) + 1
            if p.get("feedback_verdict"):
                v = p["feedback_verdict"]
                stats["by_verdict"][v] = stats["by_verdict"].get(v, 0) + 1
            if kind == "analysis" and (p.get("occurrence_count") or 0) > 1:
                recurring.append({
                    "point_id": str(r.id), "host": p.get("host"),
                    "occurrence_count": p.get("occurrence_count"),
                    "symptom_text": (p.get("symptom_text") or "")[:120],
                })
        stats["top_recurring"] = sorted(
            recurring, key=lambda x: x["occurrence_count"], reverse=True)[:10]
        return stats
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail={"error": f"memory unavailable: {exc}"})


@router.post("/api/memory/{point_id}/deprecate")
async def deprecate_point(point_id: str, tenant_id: str = "internal", note: str | None = None):
    """Retire a case an operator knows is no longer valid.

    Needed because time decay alone can't do it: a case verified eight months
    ago still carries the 1.6x verified multiplier, so after (say) a MySQL
    upgrade made its fix obsolete it can keep surfacing anyway.
    """
    point = _load_point(point_id)
    kind = (point.payload or {}).get("kind")

    if kind == KIND_PLAYBOOK:
        # Per-tenant, in SQLite — see feedback_store for why the flag can't live
        # on the shared point.
        feedback_store.deprecate_playbook_for_tenant(tenant_id, point_id, note)
        logger.info("playbook %s deprecated for tenant %s", point_id, tenant_id)
        return {"ok": True, "scope": "tenant", "point_id": point_id}

    try:
        _store().deprecate(point_id)
    except Exception as exc:
        raise HTTPException(status_code=503, detail={"error": f"memory write failed: {exc}"})
    logger.info("memory point %s deprecated", point_id)
    return {"ok": True, "scope": "global", "point_id": point_id}


@router.delete("/api/memory/{point_id}")
async def delete_point(point_id: str, confirm: bool = False):
    if not confirm:
        raise HTTPException(
            status_code=400,
            detail={"error": "destructive — pass ?confirm=true. "
                             "พิจารณา /deprecate ก่อน ซึ่งย้อนกลับได้"},
        )
    point = _load_point(point_id)
    if (point.payload or {}).get("kind") == KIND_PLAYBOOK:
        raise HTTPException(
            status_code=409,
            detail={"error": "a playbook would come back on the next seed — "
                             "ใช้ /deprecate แทน หรือลบ entry ออกจากไฟล์"},
        )
    store = _store()
    try:
        store.client.delete(store.collection, points_selector=[point_id])
    except Exception as exc:
        raise HTTPException(status_code=503, detail={"error": f"memory delete failed: {exc}"})
    logger.info("memory point %s deleted", point_id)
    return {"ok": True, "point_id": point_id}
