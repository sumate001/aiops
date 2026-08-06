#!/usr/bin/env python
"""Seed the shipped playbooks into the memory collection.

Run after editing anything under app/knowledge/playbooks/. Safe to re-run: point
ids are derived from the entry id, so a second run replaces in place rather than
creating a second copy of the same advice.

    python scripts/seed_playbooks.py                  # all engines
    python scripts/seed_playbooks.py --engine mysql   # just one
    python scripts/seed_playbooks.py --dry-run        # show what would be sent
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import config                              # noqa: E402
from app.knowledge import playbooks                        # noqa: E402
from app.services.embedder import Embedder                 # noqa: E402
from app.services.memory_store import MemoryStore          # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("seed")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--engine", action="append", help="limit to an engine (repeatable)")
    ap.add_argument("--dry-run", action="store_true", help="print, don't write")
    ap.add_argument("--qdrant-url", default=None, help="override memory.qdrant_url")
    args = ap.parse_args()

    engines = args.engine or None
    try:
        entries = playbooks.all_entries(engines)
    except (ValueError, TypeError) as exc:
        log.error("playbook validation failed: %s", exc)
        return 2

    if not entries:
        log.error("no playbooks matched %s (available: %s)",
                  engines, ", ".join(playbooks.available_engines()))
        return 2

    version = config.memory.playbook_pack.version
    by_engine: dict[str, int] = {}
    for e in entries:
        by_engine[e["engine"]] = by_engine.get(e["engine"], 0) + 1
    log.info("%d playbooks, version %s — %s", len(entries), version,
             ", ".join(f"{k}:{v}" for k, v in sorted(by_engine.items())))

    if args.dry_run:
        for e in entries:
            print(f"  {e['id']:44} {e['frame']:9} {e['severity']:8} {e['title']}")
        log.info("dry run — nothing written")
        return 0

    store = MemoryStore(
        url=args.qdrant_url or config.memory.qdrant_url,
        collection=config.memory.collection,
        embedder=Embedder(
            model_name=config.memory.embedding_model,
            device=config.memory.embedding_device,
        ),
        sparse_model=config.memory.sparse_model,
        # Seeding embeds and uploads dozens of points at once; the 2s search
        # timeout is far too tight for that.
        timeout=120.0,
    )

    if not store.health():
        log.error("Qdrant unreachable at %s", store.url)
        return 1

    store.ensure_collection()
    written = store.upsert_playbooks(entries, version)
    total = store.client.count(config.memory.collection).count
    log.info("seeded %d playbooks — collection now holds %d points", written, total)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
