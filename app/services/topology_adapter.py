"""
แปลง topology format ของ GodEye → TopologyIngestRequest ภายใน

GodEye ห่อเป็น {"network_topology": {nodes, edges}, "service_dependency": {nodes, edges}}
- node: {id, label, role, ip?, os, type, x/y/w/h (layout — ทิ้ง)}
- edge: {id, src, dst, proto, port, critical, rate}
  ทิศของ GodEye คือ "src เรียกใช้/พึ่งพา dst" — สลับเป็น source(ถูกพึ่งพา)/target(dependent)
"""

import re
from typing import Any

_LAYER_KEYS = {"network_topology": "network", "service_dependency": "service"}

# "MySQL 8.0", "Oracle 19c", "Windows Server 2019", "Java 17" → (name, version)
_VERSION_RE = re.compile(r"^(.*[^\s])\s+(v?\d[\w.\-]*)$")


def is_godeye_format(payload: dict) -> bool:
    return any(k in payload for k in _LAYER_KEYS)


def _split_version(text: str) -> dict:
    m = _VERSION_RE.match(text.strip())
    if m:
        return {"name": m.group(1), "version": m.group(2)}
    return {"name": text.strip()}


def _convert_node(raw: dict) -> dict:
    node: dict[str, Any] = {
        "node_id": raw["id"],
        "node_type": raw.get("type"),
        "service": raw.get("label"),
        "role": raw.get("role"),
    }
    # host aliases สำหรับ match กับ field `host` ใน log/metric:
    # node_id เสมอ, ip เดี่ยว (ไม่ใช่ range/masked), และ label
    # (label ซ้ำข้าม node เช่น "POS Terminals" หลายสาขา → ตัวหลังทับ, ยอมรับได้)
    hosts = [raw["id"]]
    if raw.get("ip"):
        node["ip"] = raw["ip"]
        if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", raw["ip"]):
            hosts.append(raw["ip"])
    if raw.get("label") and raw["label"] != raw["id"]:
        hosts.append(raw["label"])
    if len(hosts) > 1:
        node["hosts"] = hosts
    os_text = (raw.get("os") or "").strip()
    if os_text and os_text.lower() != "external":
        sw = _split_version(os_text)
        node["os"] = sw
        node["software"] = [sw]  # ให้ Phase 2 research ตาม (name, version) ได้เลย
    if raw.get("critical") is not None:
        node["criticality"] = raw["critical"]
    return node


def _convert_edge(raw: dict, layer: str) -> dict:
    return {
        # GodEye: src พึ่งพา dst → ภายใน: source=ถูกพึ่งพา, target=dependent
        "source": raw["dst"],
        "target": raw["src"],
        "layer": layer,
        "relation": "connects_to" if layer == "network" else "depends_on",
        "weight": 1.0 if raw.get("critical") else 0.5,
        "proto": raw.get("proto"),
        "port": raw.get("port"),
        "critical": raw.get("critical"),
        "rate": raw.get("rate"),
    }


def convert_godeye(payload: dict) -> dict:
    """คืน dict รูป TopologyIngestRequest — node id ซ้ำข้าม layer ใช้ตัวหลังทับ"""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    for key, layer in _LAYER_KEYS.items():
        section = payload.get(key) or {}
        for raw in section.get("nodes", []):
            nodes[raw["id"]] = _convert_node(raw)
        for raw in section.get("edges", []):
            edges.append(_convert_edge(raw, layer))
    return {
        "tenant_id": payload.get("tenant_id", "internal"),
        "topology_version": payload.get("topology_version"),
        "nodes": list(nodes.values()),
        "edges": edges,
    }
