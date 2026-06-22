"""
Webhook callback — ส่ง AnalyzeResponse กลับไปหา GodEye หลัง analyze เสร็จ
Fire-and-forget: ไม่ block response, ไม่ raise exception ถ้า callback ล้มเหลว
"""

import logging
import httpx

logger = logging.getLogger(__name__)

_TIMEOUT = 10.0


async def send(callback_url: str, payload: dict) -> None:
    """POST AnalyzeResponse JSON ไปยัง callback_url — graceful fallback on error"""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(callback_url, json=payload)
            if resp.status_code >= 400:
                logger.warning(
                    "Webhook callback %s returned HTTP %s", callback_url, resp.status_code
                )
            else:
                logger.info("Webhook callback sent → %s (%s)", callback_url, resp.status_code)
    except httpx.ConnectError:
        logger.warning("Webhook callback unreachable: %s", callback_url)
    except Exception as exc:
        logger.warning("Webhook callback error (%s): %s", callback_url, exc)
