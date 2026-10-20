"""Transactional-outbox relay: ships committed events to SQS. At-least-once; consumers dedupe."""

import asyncio
import logging

from sqlalchemy import select

from app.aws.clients import send_messages
from app.config import now
from app.db import SessionLocal
from app.domain.models import OutboxEvent

log = logging.getLogger("converse.outbox")


async def relay_once(batch: int = 50) -> int:
    async with SessionLocal() as db:
        rows = (await db.execute(
            select(OutboxEvent).where(OutboxEvent.sent_at.is_(None)).order_by(OutboxEvent.created_at)
            .limit(batch).with_for_update(skip_locked=True))).scalars().all()
        if not rows:
            return 0
        by_queue: dict[str, list[OutboxEvent]] = {}
        for r in rows:
            by_queue.setdefault(r.queue, []).append(r)
        for queue, evs in by_queue.items():
            await send_messages(queue, [e.body for e in evs])
            for e in evs:
                e.sent_at = now()
        await db.commit()
        return len(rows)


async def relay_forever(interval_s: float = 1.0) -> None:
    while True:
        try:
            await relay_once()
        except Exception as e:  # SQS down -> events stay in the outbox and are retried
            log.warning("outbox relay failed: %s", e)
        await asyncio.sleep(interval_s)
