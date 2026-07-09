from app.services import propagation


def _edges():
    # db → app1, app2 (critical) ; app1 → lb ; cache → app1 (ไม่ critical)
    return [
        {"source": "db", "target": "app1", "weight": 1.0},
        {"source": "db", "target": "app2", "weight": 1.0},
        {"source": "app1", "target": "lb", "weight": 1.0},
        {"source": "cache", "target": "app1", "weight": 0.5},
    ]


def test_healthy_graph_no_incidents():
    f = propagation.simulate({"db": 100.0}, _edges())
    assert f["incidents"] == []


def test_dead_db_cascades_with_eta_ordering():
    f = propagation.simulate({"db": 0.0}, _edges())
    ids = [i["node_id"] for i in f["incidents"]]
    assert set(ids) == {"app1", "app2", "lb"}

    app1 = next(i for i in f["incidents"] if i["node_id"] == "app1")
    lb = next(i for i in f["incidents"] if i["node_id"] == "lb")
    # ปลายน้ำต้องล้มช้ากว่าต้นน้ำ (การลามใช้เวลา)
    assert app1["crit_at_minute"] < lb["crit_at_minute"]
    # chain ต้องชี้กลับถึงต้นตอ
    assert lb["caused_by"][0] == "db"
    assert lb["caused_by"][-1] == "lb"


def test_deterministic_repeatable():
    a = propagation.simulate({"db": 20.0}, _edges())
    b = propagation.simulate({"db": 20.0}, _edges())
    assert a == b


def test_weight_scales_impact():
    strong = propagation.simulate(
        {"db": 0.0}, [{"source": "db", "target": "app", "weight": 1.0}])
    weak = propagation.simulate(
        {"db": 0.0}, [{"source": "db", "target": "app", "weight": 0.5}])
    s = next(i for i in strong["incidents"] if i["node_id"] == "app")
    w = next(i for i in weak["incidents"] if i["node_id"] == "app")
    assert s["crit_at_minute"] < w["crit_at_minute"]


def test_mild_degradation_may_warn_but_not_crit():
    f = propagation.simulate(
        {"db": 80.0}, [{"source": "db", "target": "app", "weight": 0.5}],
        horizon_minutes=30,
    )
    if f["incidents"]:
        inc = f["incidents"][0]
        assert inc["crit_at_minute"] is None or inc["crit_at_minute"] > 10


def test_trend_forecasts_healthy_seed_going_down():
    # seed ยัง 100 แต่ trend ดิ่ง -3 แต้ม/นาที → ควรพยากรณ์ว่าจะ crit
    f = propagation.simulate(
        {"db": 100.0}, _edges(), horizon_minutes=40,
        trends={"db": -3.0},
    )
    db = next((i for i in f["incidents"] if i["node_id"] == "db"), None)
    assert db is not None, "seed ที่ trend ดิ่งต้องถูกพยากรณ์"
    assert db["trigger"] == "trend"
    assert db["crit_at_minute"] is not None
    # และการเสื่อมของ db ต้องลามต่อไป app ด้วย
    assert any(i["node_id"] == "app1" and i["trigger"] == "propagation"
               for i in f["incidents"])


def test_no_trend_no_selfforecast():
    # seed 100 ไม่มี trend → ไม่ควรมี incident (กราฟ healthy)
    f = propagation.simulate({"db": 100.0}, _edges(), trends={"db": 0.0})
    assert f["incidents"] == []


def test_positive_trend_ignored():
    # trend เป็นบวก (กำลังฟื้น) → ไม่พยากรณ์เป็น incident
    f = propagation.simulate({"db": 90.0}, _edges(), trends={"db": 5.0})
    assert all(i["node_id"] != "db" for i in f["incidents"])


def test_describe_lines_use_labels():
    f = propagation.simulate({"db": 0.0}, _edges())
    lines = propagation.describe(f, {"db": "Payment DB", "app1": "Payment GW"})
    joined = "\n".join(lines)
    assert "Payment DB (db)" in joined
    assert "critical in ~" in joined
