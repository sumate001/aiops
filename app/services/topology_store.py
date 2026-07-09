"""
SQLite store สำหรับ topology (Phase 1) — node profiles + dependency edges

node profile ต่อ node เก็บเป็น JSON เต็ม (schema จาก GodEye ยังไม่นิ่ง)
ส่วน column ที่ต้อง query ได้ (node_id, host mapping) แยกออกมาเป็น column จริง
"""

import json
import logging
import sqlite3
from collections import deque
from datetime import datetime, timezone

from app.models.topology import TopologyEdge, TopologyNode

logger = logging.getLogger(__name__)

_DB_PATH = "log_analyzer.db"


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(_DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_topology_tables() -> None:
    with _conn() as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS topology_nodes (
                tenant_id   TEXT NOT NULL,
                node_id     TEXT NOT NULL,
                profile     TEXT NOT NULL,          -- TopologyNode JSON เต็ม
                updated_at  TEXT NOT NULL,
                PRIMARY KEY (tenant_id, node_id)
            );
            CREATE TABLE IF NOT EXISTS topology_hosts (
                tenant_id   TEXT NOT NULL,
                host        TEXT NOT NULL,          -- ค่าใน field `host` ของ log/metric
                node_id     TEXT NOT NULL,
                PRIMARY KEY (tenant_id, host)
            );
            CREATE TABLE IF NOT EXISTS topology_edges (
                tenant_id   TEXT NOT NULL,
                source      TEXT NOT NULL,
                target      TEXT NOT NULL,
                layer       TEXT NOT NULL DEFAULT 'service',
                relation    TEXT NOT NULL DEFAULT 'depends_on',
                weight      REAL,
                extra       TEXT,
                PRIMARY KEY (tenant_id, source, target, layer, relation)
            );
            CREATE TABLE IF NOT EXISTS topology_meta (
                tenant_id         TEXT PRIMARY KEY,
                topology_version  TEXT,
                updated_at        TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_te_target
                ON topology_edges(tenant_id, target);
        """)


def save_topology(
    tenant_id: str,
    nodes: list[TopologyNode],
    edges: list[TopologyEdge],
    topology_version: str | None = None,
) -> dict:
    """Upsert nodes ทั้งชุด; replace edges เฉพาะ layer ที่ปรากฏใน upload
    เพื่อให้ GodEye ส่ง network/service topology แยกรอบกันได้"""
    now = datetime.now(timezone.utc).isoformat()
    layers = {e.layer for e in edges}

    with _conn() as c:
        for n in nodes:
            c.execute(
                """INSERT INTO topology_nodes (tenant_id, node_id, profile, updated_at)
                   VALUES (?,?,?,?)
                   ON CONFLICT(tenant_id, node_id)
                   DO UPDATE SET profile=excluded.profile, updated_at=excluded.updated_at""",
                (tenant_id, n.node_id, n.model_dump_json(), now),
            )
            c.execute(
                "DELETE FROM topology_hosts WHERE tenant_id=? AND node_id=?",
                (tenant_id, n.node_id),
            )
            for host in n.all_hosts():
                c.execute(
                    """INSERT INTO topology_hosts (tenant_id, host, node_id)
                       VALUES (?,?,?)
                       ON CONFLICT(tenant_id, host) DO UPDATE SET node_id=excluded.node_id""",
                    (tenant_id, host, n.node_id),
                )

        for layer in layers:
            c.execute(
                "DELETE FROM topology_edges WHERE tenant_id=? AND layer=?",
                (tenant_id, layer),
            )
        for e in edges:
            extra = e.model_dump(exclude={"source", "target", "layer", "relation", "weight"})
            c.execute(
                """INSERT OR REPLACE INTO topology_edges
                       (tenant_id, source, target, layer, relation, weight, extra)
                   VALUES (?,?,?,?,?,?,?)""",
                (tenant_id, e.source, e.target, e.layer, e.relation, e.weight,
                 json.dumps(extra) if extra else None),
            )

        c.execute(
            """INSERT INTO topology_meta (tenant_id, topology_version, updated_at)
               VALUES (?,?,?)
               ON CONFLICT(tenant_id)
               DO UPDATE SET topology_version=excluded.topology_version,
                             updated_at=excluded.updated_at""",
            (tenant_id, topology_version, now),
        )

    logger.info(
        "Topology saved tenant=%s nodes=%d edges=%d layers=%s version=%s",
        tenant_id, len(nodes), len(edges), sorted(layers) or "-", topology_version,
    )
    return {"nodes": len(nodes), "edges": len(edges), "layers": sorted(layers)}


def _edge_dict(r: sqlite3.Row) -> dict:
    d = {
        "source": r["source"], "target": r["target"],
        "layer": r["layer"], "relation": r["relation"], "weight": r["weight"],
    }
    if r["extra"]:
        d.update(json.loads(r["extra"]))
    return d


def get_topology(tenant_id: str) -> dict | None:
    with _conn() as c:
        meta = c.execute(
            "SELECT * FROM topology_meta WHERE tenant_id=?", (tenant_id,)
        ).fetchone()
        if not meta:
            return None
        nodes = c.execute(
            "SELECT profile FROM topology_nodes WHERE tenant_id=?", (tenant_id,)
        ).fetchall()
        edges = c.execute(
            "SELECT * FROM topology_edges WHERE tenant_id=?", (tenant_id,)
        ).fetchall()
    return {
        "tenant_id": tenant_id,
        "topology_version": meta["topology_version"],
        "updated_at": meta["updated_at"],
        "nodes": [json.loads(r["profile"]) for r in nodes],
        "edges": [_edge_dict(r) for r in edges],
    }


def get_node(tenant_id: str, node_id: str) -> dict | None:
    with _conn() as c:
        row = c.execute(
            "SELECT profile FROM topology_nodes WHERE tenant_id=? AND node_id=?",
            (tenant_id, node_id),
        ).fetchone()
    return json.loads(row["profile"]) if row else None


def resolve_host(tenant_id: str, host: str) -> dict | None:
    """map ค่า `host` จาก log/metric → node profile (จุดเชื่อม pipeline เดิม)"""
    with _conn() as c:
        row = c.execute(
            """SELECT n.profile FROM topology_hosts h
               JOIN topology_nodes n ON n.tenant_id=h.tenant_id AND n.node_id=h.node_id
               WHERE h.tenant_id=? AND h.host=?""",
            (tenant_id, host),
        ).fetchone()
    return json.loads(row["profile"]) if row else None


def host_map(tenant_id: str) -> dict[str, str]:
    """host (จาก log/metric) → node_id ทั้ง tenant — ใช้ seed propagation"""
    with _conn() as c:
        rows = c.execute(
            "SELECT host, node_id FROM topology_hosts WHERE tenant_id=?", (tenant_id,)
        ).fetchall()
    return {r["host"]: r["node_id"] for r in rows}


def node_labels(tenant_id: str) -> dict[str, str]:
    """node_id → ชื่ออ่านง่าย (service/label) สำหรับรายงานผล"""
    with _conn() as c:
        rows = c.execute(
            "SELECT node_id, profile FROM topology_nodes WHERE tenant_id=?", (tenant_id,)
        ).fetchall()
    out = {}
    for r in rows:
        p = json.loads(r["profile"])
        out[r["node_id"]] = p.get("service") or r["node_id"]
    return out


def get_dependents(tenant_id: str, node_id: str, transitive: bool = False) -> list[dict]:
    """ใครพึ่งพา node นี้บ้าง (node นี้ล่มแล้วใครโดนหางเลข)"""
    return _walk(tenant_id, node_id, from_col="source", to_col="target",
                 transitive=transitive)


def get_dependencies(tenant_id: str, node_id: str, transitive: bool = False) -> list[dict]:
    """node นี้พึ่งพาใครบ้าง (ต้นตอที่อาจทำให้ node นี้พัง)"""
    return _walk(tenant_id, node_id, from_col="target", to_col="source",
                 transitive=transitive)


def _walk(tenant_id: str, node_id: str, from_col: str, to_col: str,
          transitive: bool) -> list[dict]:
    with _conn() as c:
        seen: set[str] = {node_id}
        out: list[dict] = []
        queue = deque([(node_id, 0)])
        while queue:
            current, depth = queue.popleft()
            rows = c.execute(
                f"SELECT * FROM topology_edges WHERE tenant_id=? AND {from_col}=?",
                (tenant_id, current),
            ).fetchall()
            for r in rows:
                nxt = r[to_col]
                if nxt in seen:
                    continue
                seen.add(nxt)
                d = _edge_dict(r)
                d["depth"] = depth + 1
                d["node_id"] = nxt
                out.append(d)
                if transitive:
                    queue.append((nxt, depth + 1))
    return out
