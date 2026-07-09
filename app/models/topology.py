"""
Schema สำหรับรับ topology จาก GodEye (Phase 1 ของ topology-aware prediction)

สมมุติฐาน: GodEye ส่ง network topology กับ service topology มาเป็น JSON
(อาจแยกกันคนละ upload) — schema จึงเปิด extra="allow" ทุกตัว เพราะ format
จริงยัง finalize ไม่ได้ ดู docs/prediction-topology-simulation-proposal.md ข้อ 5
"""

from pydantic import BaseModel, Field
from typing import Any, Literal


class SoftwareInfo(BaseModel):
    """หน่วยความรู้ของ Phase 2: research key คือ (name, version)"""

    model_config = {"extra": "allow"}

    name: str
    version: str | None = None
    role: str | None = None  # e.g. "database", "web", "cache"


class TopologyNode(BaseModel):
    model_config = {"extra": "allow"}

    node_id: str
    # hostnames/aliases ที่ปรากฏใน field `host` ของ log/metric — ใช้ match กับ
    # pipeline เดิม; ถ้าว่างจะถือว่า node_id คือ host ตรงๆ
    hosts: list[str] = Field(default_factory=list)

    node_type: str | None = None  # e.g. "vm", "container", "switch", "router"
    service: str | None = None
    criticality: str | None = None

    os: SoftwareInfo | None = None
    hardware: dict[str, Any] | None = None
    software: list[SoftwareInfo] = Field(default_factory=list)

    def all_hosts(self) -> list[str]:
        return self.hosts or [self.node_id]


class TopologyEdge(BaseModel):
    model_config = {"extra": "allow"}

    source: str  # node_id ฝั่งที่ "ถูกพึ่งพา" เช่น db
    target: str  # node_id ฝั่งที่ depend on source เช่น app
    layer: Literal["service", "network"] = "service"
    relation: str = "depends_on"  # หรือ "connects_to" ฝั่ง network
    weight: float | None = None   # น้ำหนักการลาม (จูนใน Phase 3)


class TopologyIngestRequest(BaseModel):
    """POST /topology — snapshot จาก GodEye

    Replace semantics: nodes ทั้งหมดของ tenant ถูก upsert;
    edges ถูก replace เฉพาะ layer ที่ปรากฏใน request (upload network กับ
    service แยกกันได้โดยไม่ลบของอีก layer)
    """

    tenant_id: str = "internal"
    topology_version: str | None = None  # version/timestamp ของ snapshot จาก GodEye
    nodes: list[TopologyNode] = Field(min_length=1)
    edges: list[TopologyEdge] = Field(default_factory=list)
