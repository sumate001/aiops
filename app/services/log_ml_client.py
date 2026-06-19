"""
HTTP client สำหรับ log-ml :3050 — ส่ง window_stats และรับ IF anomaly score กลับ
Graceful fallback: ถ้า log-ml ไม่ up ให้ return None ไม่ raise exception
"""

import logging
import httpx

logger = logging.getLogger(__name__)


async def score_window(
    host: str,
    tenant_id: str,
    window_from: str,
    window_to: str,
    entry_count: int,
    error_count: int,
    warn_count: int,
    health_score: float,
    crash_count: int = 0,
    auth_fail_count: int = 0,
    payment_fail_count: int = 0,
    network_err_count: int = 0,
    db_err_count: int = 0,
    hardware_err_count: int = 0,
    app_crash_count: int = 0,
    base_url: str = "http://localhost:3050",
    timeout: float = 10.0,
) -> dict | None:
    """
    ส่ง 1 window ไปให้ log-ml score
    Returns: {"anomaly_score": float, "is_anomaly": bool, "method": str, "features": dict}
    หรือ None ถ้า log-ml ไม่ตอบสนอง
    """
    payload = {
        "host": host,
        "tenant_id": tenant_id,
        "windows": [{
            "window_from":        window_from,
            "window_to":          window_to,
            "entry_count":        entry_count,
            "error_count":        error_count,
            "warn_count":         warn_count,
            "health_score":       health_score,
            "crash_count":        crash_count,
            "auth_fail_count":    auth_fail_count,
            "payment_fail_count": payment_fail_count,
            "network_err_count":  network_err_count,
            "db_err_count":       db_err_count,
            "hardware_err_count": hardware_err_count,
            "app_crash_count":    app_crash_count,
        }],
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(f"{base_url}/anomalies", json=payload)
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])
            if results:
                r = results[0]
                return {
                    "anomaly_score": r["anomaly_score"],
                    "is_anomaly":    r["is_anomaly"],
                    "method":        r["method"],
                    "features":      r.get("features", {}),
                    "model_trained": data.get("model_trained", False),
                }
    except httpx.ConnectError:
        logger.debug("log-ml not reachable at %s", base_url)
    except Exception as exc:
        logger.warning("log-ml error for %s: %s", host, exc)
    return None
