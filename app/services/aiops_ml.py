import logging

import httpx

logger = logging.getLogger(__name__)

KNOWN_PROFILES = {"linux", "postgresql", "mongodb", "windows", "db_postgres", "app_jvm"}


async def predict(
    hostnames: list[str],
    window: str,
    horizon: str,
    base_url: str,
    timeout: float = 60.0,
) -> dict | None:
    payload = {
        "hosts": {"names": hostnames},
        "window": window,
        "horizon": horizon,
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resp = await client.post(f"{base_url}/predict", json=payload)
            resp.raise_for_status()
            return resp.json()
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            logger.warning("aiops-ml /predict unreachable for %s: %s", hostnames, exc)
            return None
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "aiops-ml /predict returned %d for %s: %s",
                exc.response.status_code,
                hostnames,
                exc,
            )
            return None


async def explain(
    host: str,
    window_from: str,
    window_to: str,
    anomalies: list[dict],
    base_url: str,
    timeout: float = 60.0,
) -> dict | None:
    payload = {
        "host": host,
        "window": {"from": window_from, "to": window_to},
        "anomalies": anomalies,
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resp = await client.post(f"{base_url}/explain", json=payload)
            resp.raise_for_status()
            return resp.json()
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            logger.warning("aiops-ml /explain unreachable for %s: %s", host, exc)
            return None
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "aiops-ml /explain returned %d for %s: %s",
                exc.response.status_code,
                host,
                exc,
            )
            return None
