import pytest

from app.models.topology import TopologyIngestRequest
from app.services import topology_store


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(topology_store, "_DB_PATH", str(tmp_path / "topo.db"))
    topology_store.init_topology_tables()


def _sample_request(**overrides) -> TopologyIngestRequest:
    payload = {
        "tenant_id": "t1",
        "topology_version": "v1",
        "nodes": [
            {"node_id": "db-01", "service": "mysql", "hosts": ["db-01", "10.0.0.5"],
             "software": [{"name": "mysql", "version": "8.0.32"}]},
            {"node_id": "app-01", "service": "pos-app"},
            {"node_id": "app-02", "service": "pos-app"},
            {"node_id": "lb-01", "service": "nginx"},
        ],
        "edges": [
            {"source": "db-01", "target": "app-01"},
            {"source": "db-01", "target": "app-02"},
            {"source": "app-01", "target": "lb-01"},
        ],
    }
    payload.update(overrides)
    return TopologyIngestRequest.model_validate(payload)


def _save(req: TopologyIngestRequest) -> dict:
    return topology_store.save_topology(
        req.tenant_id, req.nodes, req.edges, req.topology_version
    )


def test_save_and_get_topology():
    stats = _save(_sample_request())
    assert stats == {"nodes": 4, "edges": 3, "layers": ["service"]}

    topo = topology_store.get_topology("t1")
    assert topo["topology_version"] == "v1"
    assert len(topo["nodes"]) == 4
    assert len(topo["edges"]) == 3


def test_resolve_host_alias_and_default():
    _save(_sample_request())
    # alias จาก hosts list
    assert topology_store.resolve_host("t1", "10.0.0.5")["node_id"] == "db-01"
    # node ไม่มี hosts → node_id คือ host
    assert topology_store.resolve_host("t1", "app-01")["node_id"] == "app-01"
    assert topology_store.resolve_host("t1", "nope") is None


def test_dependents_transitive():
    _save(_sample_request())
    direct = topology_store.get_dependents("t1", "db-01")
    assert {d["node_id"] for d in direct} == {"app-01", "app-02"}

    trans = topology_store.get_dependents("t1", "db-01", transitive=True)
    assert {d["node_id"] for d in trans} == {"app-01", "app-02", "lb-01"}
    depth = {d["node_id"]: d["depth"] for d in trans}
    assert depth["lb-01"] == 2

    deps = topology_store.get_dependencies("t1", "lb-01", transitive=True)
    assert {d["node_id"] for d in deps} == {"app-01", "db-01"}


def test_layer_scoped_replace():
    """upload network layer แยกรอบ ต้องไม่ลบ service edges เดิม"""
    _save(_sample_request())
    net = _sample_request(edges=[
        {"source": "sw-01", "target": "db-01", "layer": "network",
         "relation": "connects_to"},
    ], nodes=[{"node_id": "sw-01", "node_type": "switch"}])
    _save(net)

    topo = topology_store.get_topology("t1")
    layers = {e["layer"] for e in topo["edges"]}
    assert layers == {"service", "network"}
    assert len(topo["edges"]) == 4
    assert len(topo["nodes"]) == 5  # nodes upsert ไม่ replace

    # re-upload service layer → replace เฉพาะ service
    svc2 = _sample_request(edges=[{"source": "db-01", "target": "app-01"}])
    _save(svc2)
    topo = topology_store.get_topology("t1")
    svc_edges = [e for e in topo["edges"] if e["layer"] == "service"]
    net_edges = [e for e in topo["edges"] if e["layer"] == "network"]
    assert len(svc_edges) == 1
    assert len(net_edges) == 1


def test_tenant_isolation():
    _save(_sample_request())
    assert topology_store.get_topology("other") is None
    assert topology_store.resolve_host("other", "db-01") is None
