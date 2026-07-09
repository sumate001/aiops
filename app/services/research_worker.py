"""
Phase 2 — background research worker (ตัวเดียว ระบายคิวช้าๆ)

- 1 query ต่อ WORK_INTERVAL (default 4 นาที) → topology ~30 profile เก็บครบใน ~2 ชม.
  โดยไม่มี burst ไปชน SearXNG
- fail → backoff รายรายการใน knowledge_store + พักทั้งคิว QUEUE_PAUSE
- on-demand: research_now() สำหรับปัญหาที่ไม่เคยเจอ — ยิงทันทีไม่รอคิว
  (ยังอยู่ใต้ cooldown 60s ของ perplexica_client จึงไม่ burst)
"""

import asyncio
import logging

from app.config import config, PERPLEXICA_TIMEOUT
from app.services import knowledge_store, perplexica_client

logger = logging.getLogger(__name__)

WORK_INTERVAL = 240.0        # วินาที ระหว่าง query ปกติ
IDLE_INTERVAL = 600.0        # คิวว่าง → เช็คใหม่ทุก 10 นาที (รวม stale requeue)
QUEUE_PAUSE = 900.0          # upstream ล้ม → พักทั้งคิว 15 นาที


def _build_query(item: dict) -> str:
    if item["kind"] == "compat":
        os_part = f"{item['os_name']} {item['os_version'] or ''}".strip()
        sw_part = f"{item['name']} {item['version'] or ''}".strip()
        return (f"{sw_part} on {os_part} compatibility issues known bugs")
    sw_part = f"{item['name']} {item['version'] or ''}".strip()
    return f"{sw_part} known bugs common issues failure modes"


async def _search(query: str) -> dict | None:
    px = config.llm.resolve("perplexica")
    return await perplexica_client.search(
        query=query,
        base_url=config.perplexica.base_url,
        chat_model=px.model,
        embedding_model=config.perplexica.embedding_model,
        timeout=PERPLEXICA_TIMEOUT,
        chat_provider=px.provider,
        chat_base_url=px.base_url,
        chat_api_key=px.api_key,
        mode=config.perplexica.mode,
    )


async def process_next(search=_search) -> bool:
    """หยิบงานถัดไปจากคิวแล้ว research หนึ่งรายการ
    คืน True เมื่อสำเร็จ, False เมื่อคิวว่างหรือ fail (ให้ caller ตัดสินใจพัก)
    `search` ฉีดแทนได้ในเทส"""
    item = knowledge_store.next_pending()
    if not item:
        return False
    query = _build_query(item)
    logger.info("Research worker: %s (attempt %d) — %s",
                item["key"], item["attempts"] + 1, query)
    result = await search(query)
    if result and result.get("answer"):
        knowledge_store.mark_ok(
            item["key"], query, result["answer"], result.get("sources", []))
        logger.info("Research worker: OK %s (answer %d chars, %d sources)",
                    item["key"], len(result["answer"]), len(result.get("sources", [])))
        return True
    knowledge_store.mark_failed(item["key"])
    logger.warning("Research worker: failed %s", item["key"])
    return False


async def research_now(
    name: str,
    version: str | None,
    kind: str = "version",
    os_name: str | None = None,
    os_version: str | None = None,
) -> dict | None:
    """on-demand: เจอปัญหาที่ไม่มี knowledge → research ทันที ข้ามคิว
    ถ้ามีความรู้อยู่แล้วคืนของเดิมเลย ไม่ยิงซ้ำ"""
    known = knowledge_store.get_knowledge(name, version)
    if known:
        return known
    knowledge_store.enqueue_profile(name, version, kind=kind,
                                    os_name=os_name, os_version=os_version,
                                    priority=100)
    await process_next()
    return knowledge_store.get_knowledge(name, version)


async def run_forever() -> None:
    """background loop — start จาก lifespan"""
    logger.info("Research worker started (interval %.0fs)", WORK_INTERVAL)
    # หน่วงรอบแรก — ไม่ชน A2 warm-up (cooldown 60s ฝั่ง perplexica_client)
    await asyncio.sleep(90)
    while True:
        try:
            if not (config.perplexica.enabled and config.llm.enabled):
                await asyncio.sleep(IDLE_INTERVAL)
                continue
            requeued = knowledge_store.requeue_stale()
            if requeued:
                logger.info("Research worker: %d stale profiles requeued", requeued)
            if knowledge_store.next_pending() is None:
                await asyncio.sleep(IDLE_INTERVAL)
                continue
            ok = await process_next()
            await asyncio.sleep(WORK_INTERVAL if ok else QUEUE_PAUSE)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Research worker: unexpected error — pausing")
            await asyncio.sleep(QUEUE_PAUSE)
