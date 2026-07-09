"""
POST /topology — รับ network/service topology snapshot จาก GodEye (Phase 1)
GET  /topology — ดู snapshot ปัจจุบัน
GET  /topology/node/{node_id} — profile + dependencies/dependents ของ node
"""

import logging

from fastapi import APIRouter, HTTPException

from app.models.topology import TopologyIngestRequest
from app.services import knowledge_store, topology_store
from app.services.topology_adapter import convert_godeye, is_godeye_format

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/topology")
async def upload_topology(raw: dict) -> dict:
    """รับได้ทั้ง format ภายใน ({nodes, edges}) และ GodEye
    ({network_topology, service_dependency})"""
    if is_godeye_format(raw):
        raw = convert_godeye(raw)
    try:
        payload = TopologyIngestRequest.model_validate(raw)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    node_ids = {n.node_id for n in payload.nodes}
    unknown = [
        e for e in payload.edges
        if e.source not in node_ids or e.target not in node_ids
    ]
    # edge อ้าง node ที่ไม่อยู่ใน upload นี้ อาจอยู่ใน snapshot เดิม (upload แยก layer)
    # จึงไม่ reject — แค่เตือนไว้ใน response
    stats = topology_store.save_topology(
        tenant_id=payload.tenant_id,
        nodes=payload.nodes,
        edges=payload.edges,
        topology_version=payload.topology_version,
    )
    queued = knowledge_store.enqueue_from_topology(payload.tenant_id)
    return {
        "knowledge_profiles_queued": queued,
        "status": "ok",
        "tenant_id": payload.tenant_id,
        "topology_version": payload.topology_version,
        **stats,
        "edges_referencing_unknown_nodes": len(unknown),
    }


@router.get("/knowledge")
async def knowledge_status() -> dict:
    """สถานะคิว research ของ Phase 2 (pending/ok/failed)"""
    return {"queue": knowledge_store.queue_stats()}


@router.get("/topology")
async def read_topology(tenant_id: str = "internal") -> dict:
    topo = topology_store.get_topology(tenant_id)
    if topo is None:
        raise HTTPException(status_code=404, detail=f"no topology for tenant '{tenant_id}'")
    return topo


@router.get("/topology/node/{node_id}")
async def read_node(node_id: str, tenant_id: str = "internal") -> dict:
    profile = topology_store.get_node(tenant_id, node_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"node '{node_id}' not found")
    return {
        "profile": profile,
        "dependencies": topology_store.get_dependencies(tenant_id, node_id, transitive=True),
        "dependents": topology_store.get_dependents(tenant_id, node_id, transitive=True),
    }
