from app.services.topology_adapter import convert_godeye, is_godeye_format
from app.models.topology import TopologyIngestRequest

GODEYE_SAMPLE = {
    "network_topology": {
        "nodes": [
            {"id": "db-inv", "label": "Inventory DB", "role": "db_server",
             "ip": "10.0.3.20", "os": "MySQL 8.0", "x": 1, "y": 2, "w": 3, "h": 4,
             "type": "db"},
            {"id": "inv-api", "label": "Inventory API", "role": "app_server",
             "ip": "10.0.2.20", "os": "Ubuntu 22.04", "type": "server"},
            {"id": "ext-visa", "label": "VISA Network", "role": "external",
             "ip": "203.150.x.x", "os": "", "type": "client"},
        ],
        "edges": [
            {"id": "inv-api->db-inv", "src": "inv-api", "dst": "db-inv",
             "proto": "MySQL", "port": "3306", "critical": True, "rate": "~300/s"},
        ],
    },
    "service_dependency": {
        "nodes": [
            {"id": "inv-svc", "label": "Inventory Service", "role": "service",
             "os": "Spring Boot", "type": "service"},
            {"id": "inv-db", "label": "Inventory DB", "role": "db_server",
             "os": "MySQL 8.0", "type": "db"},
        ],
        "edges": [
            {"id": "s:inv-svc->inv-db", "src": "inv-svc", "dst": "inv-db",
             "proto": "MySQL", "port": "3306", "critical": False, "rate": ""},
        ],
    },
}


def test_detect_format():
    assert is_godeye_format(GODEYE_SAMPLE)
    assert not is_godeye_format({"nodes": []})


def test_convert_validates_and_maps():
    converted = convert_godeye({**GODEYE_SAMPLE, "tenant_id": "t1"})
    req = TopologyIngestRequest.model_validate(converted)
    assert req.tenant_id == "t1"
    ids = {n.node_id for n in req.nodes}
    assert ids == {"db-inv", "inv-api", "ext-visa", "inv-svc", "inv-db"}

    db = next(n for n in req.nodes if n.node_id == "db-inv")
    assert db.os.name == "MySQL" and db.os.version == "8.0"
    assert db.software[0].name == "MySQL"
    # ip เดี่ยว + label → host alias
    assert set(db.hosts) == {"db-inv", "10.0.3.20", "Inventory DB"}

    ext = next(n for n in req.nodes if n.node_id == "ext-visa")
    assert ext.os is None            # os ว่าง → ไม่มี software profile
    assert set(ext.hosts) == {"ext-visa", "VISA Network"}  # ip masked → ไม่เป็น alias


def test_edge_direction_flipped_per_layer():
    req = TopologyIngestRequest.model_validate(convert_godeye(GODEYE_SAMPLE))
    net = next(e for e in req.edges if e.layer == "network")
    # GodEye: inv-api (src) พึ่งพา db-inv (dst) → source=db-inv, target=inv-api
    assert (net.source, net.target) == ("db-inv", "inv-api")
    assert net.relation == "connects_to"
    assert net.weight == 1.0  # critical

    svc = next(e for e in req.edges if e.layer == "service")
    assert (svc.source, svc.target) == ("inv-db", "inv-svc")
    assert svc.relation == "depends_on"
    assert svc.weight == 0.5  # non-critical
