"""
Phase 3 — graph propagation engine (deterministic, โค้ดล้วน ไม่มี LLM)

จำลองการลามของปัญหาบน topology ทีละนาที:
- node เริ่มจาก seed health (ผลวิเคราะห์จริงจาก pipeline) — node ที่ไม่มีข้อมูลถือว่า 100
- ทุกนาที dependent ถูกดึง health ลงตาม deficit ของ dependency:
    impact = IMPACT_PTS_PER_MIN × edge.weight × (100 − health(source)) / 100
- ทำซ้ำได้ 100% (run เดิม ผลเดิม) → backtest/จูนพารามิเตอร์ได้

LLM ไม่อยู่ในลูปนี้ — ใช้แค่ "ท้าย" (judge อ่านผล forecast เป็นหลักฐาน)
"""

from dataclasses import dataclass, field

# จูนได้ภายหลังจาก incident จริง (ดู proposal ข้อ 5)
IMPACT_PTS_PER_MIN = 8.0   # dependency ตายสนิท (health 0, weight 1) ดึงลง 8 แต้ม/นาที
WARN_AT = 70.0
CRIT_AT = 50.0
DEFAULT_HORIZON_MIN = 30


@dataclass
class NodeForecast:
    node_id: str
    start_health: float
    end_health: float
    warn_at_minute: int | None = None      # นาทีแรกที่หลุดต่ำกว่า WARN_AT
    crit_at_minute: int | None = None
    caused_by: list[str] = field(default_factory=list)  # chain จากต้นตอ → node นี้
    trigger: str = "propagation"           # "trend" (เสื่อมเองตามแนวโน้ม) | "propagation"


def simulate(
    seeds: dict[str, float],
    edges: list[dict],
    horizon_minutes: int = DEFAULT_HORIZON_MIN,
    trends: dict[str, float] | None = None,
) -> dict:
    """
    seeds: node_id → health ปัจจุบัน (เฉพาะ node ที่มีข้อมูลจริง)
    edges: [{source, target, weight, ...}] — source คือตัวถูกพึ่งพา
    trends: node_id → health slope (แต้ม/นาที, ติดลบ = กำลังเสื่อม) จากประวัติ
            window จริง — seed node จะเสื่อมต่อตาม trend ของตัวเองระหว่าง
            simulation ทำให้ host ที่ "ยังไม่แย่แต่กำลังดิ่ง" พยากรณ์ล่วงหน้าได้
    คืน dict forecast (JSON-ready)
    """
    trends = trends or {}
    # สร้าง universe จาก edges + seeds
    node_ids: set[str] = set(seeds)
    for e in edges:
        node_ids.add(e["source"])
        node_ids.add(e["target"])

    health: dict[str, float] = {n: float(seeds.get(n, 100.0)) for n in node_ids}
    start = dict(health)
    # dominant_cause[n] = (source ที่กดดันมากสุด) — ไว้สร้าง chain
    dominant_cause: dict[str, str] = {}
    warn_minute: dict[str, int] = {}
    crit_minute: dict[str, int] = {}

    def _record_crossing(node: str, new_health: float, minute: int) -> None:
        if new_health < WARN_AT and node not in warn_minute:
            warn_minute[node] = minute
        if new_health < CRIT_AT and node not in crit_minute:
            crit_minute[node] = minute

    for minute in range(1, horizon_minutes + 1):
        # ── การเสื่อมของ seed เองตาม trend จริง (พยากรณ์ต้นตอ) ──
        trending = False
        for n, slope in trends.items():
            if n not in health or slope >= 0 or health[n] <= 0.0:
                continue
            trending = True
            new_health = max(0.0, health[n] + slope)
            if new_health < health[n]:
                health[n] = new_health
                _record_crossing(n, new_health, minute)

        # ── การลามผ่าน dependency edges ──
        pressure: dict[str, float] = {}
        strongest: dict[str, tuple[float, str]] = {}
        for e in edges:
            deficit = max(0.0, 100.0 - health[e["source"]]) / 100.0
            if deficit <= 0.0:
                continue
            w = e.get("weight") or 1.0
            impact = IMPACT_PTS_PER_MIN * w * deficit
            pressure[e["target"]] = pressure.get(e["target"], 0.0) + impact
            if impact > strongest.get(e["target"], (0.0, ""))[0]:
                strongest[e["target"]] = (impact, e["source"])

        if not pressure and not trending:
            break  # ไม่มีอะไรลามหรือเสื่อมต่อแล้ว

        for target, pts in pressure.items():
            new_health = max(0.0, health[target] - pts)
            if new_health < health[target]:
                health[target] = new_health
                dominant_cause.setdefault(target, strongest[target][1])
                _record_crossing(target, new_health, minute)

    def _chain(node: str) -> list[str]:
        chain = [node]
        seen = {node}
        while chain[-1] in dominant_cause:
            nxt = dominant_cause[chain[-1]]
            if nxt in seen:
                break
            chain.append(nxt)
            seen.add(nxt)
        chain.reverse()  # ต้นตอ → ปลายทาง
        return chain

    forecasts = []
    for n in sorted(node_ids):
        # รายงานเฉพาะ node ที่ "จะแย่ลง" ระหว่าง horizon (ข้าม seed ที่แย่อยู่แล้ว
        # และไม่โดนอะไรเพิ่ม — พวกนั้นแสดงใน host analysis อยู่แล้ว)
        if n in warn_minute or n in crit_minute:
            chain = _chain(n)
            # ต้นตอมาจาก trend ของตัวเอง (ไม่มี upstream cause) = พยากรณ์จากแนวโน้ม
            trigger = "trend" if len(chain) == 1 and n in trends else "propagation"
            forecasts.append(NodeForecast(
                node_id=n,
                start_health=round(start[n], 1),
                end_health=round(health[n], 1),
                warn_at_minute=warn_minute.get(n),
                crit_at_minute=crit_minute.get(n),
                caused_by=chain,
                trigger=trigger,
            ))

    # เรียงตามความเร่งด่วน: crit เร็วสุดก่อน
    forecasts.sort(key=lambda f: (f.crit_at_minute or 999, f.warn_at_minute or 999))

    return {
        "engine": "graph-propagation-v1",
        "horizon_minutes": horizon_minutes,
        "seeded": {k: round(v, 1) for k, v in seeds.items()},
        "incidents": [
            {
                "node_id": f.node_id,
                "start_health": f.start_health,
                "end_health": f.end_health,
                "warn_at_minute": f.warn_at_minute,
                "crit_at_minute": f.crit_at_minute,
                "caused_by": f.caused_by,
                "trigger": f.trigger,
            }
            for f in forecasts
        ],
    }


def describe(forecast: dict, node_labels: dict[str, str] | None = None) -> list[str]:
    """แปลง forecast เป็นบรรทัดข้อความสำหรับ judge prompt / summary"""
    labels = node_labels or {}

    def _name(n: str) -> str:
        return f"{labels[n]} ({n})" if n in labels and labels[n] != n else n

    lines = []
    for inc in forecast.get("incidents", []):
        if inc["crit_at_minute"] is not None:
            eta = f"critical in ~{inc['crit_at_minute']} min"
        else:
            eta = f"degraded in ~{inc['warn_at_minute']} min"
        if inc.get("trigger") == "trend":
            # พยากรณ์จากแนวโน้มของตัวเอง (ยังไม่ critical ตอนนี้ แต่กำลังดิ่ง)
            lines.append(
                f"{_name(inc['node_id'])}: forecast {eta} based on its own "
                f"degradation trend (health {inc['start_health']} -> {inc['end_health']})"
            )
        else:
            chain = " -> ".join(_name(n) for n in inc["caused_by"])
            lines.append(
                f"{_name(inc['node_id'])}: {eta} "
                f"(health {inc['start_health']} -> {inc['end_health']}) via {chain}"
            )
    return lines
