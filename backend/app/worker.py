"""SQS consumer: order events -> notifications + popularity signal; reminder events -> notifications.

    uv run python -m app.worker
"""

import asyncio
import json
import logging

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from app.aws.clients import client, queue_url
from app.config import get_settings
from app.db import SessionLocal
from app.domain.models import Notification, ProcessedEvent, Product

log = logging.getLogger("converse.worker")


async def handle(body: dict) -> None:
    event_id = body.get("event_id") or f"{body.get('type')}:{body.get('reminder_id')}:{body.get('fired_at', '')}"
    async with SessionLocal() as db:
        db.add(ProcessedEvent(event_id=event_id))
        try:
            await db.flush()
        except IntegrityError:  # duplicate delivery: already processed
            await db.rollback()
            log.info("skip duplicate %s", event_id)
            return
        if body["type"] == "order.placed":
            for it in body["items"]:
                await db.execute(update(Product).where(Product.id == it["product_id"])
                                 .values(popularity=Product.popularity + it["quantity"]))
            db.add(Notification(user_id=body["user_id"], kind="order",
                                body=f"Order #{body['order_id']} confirmed · ₹{body['total']:.0f}. Arriving in 10 minutes."))
        elif body["type"] == "reminder.due":
            db.add(Notification(user_id=body["user_id"], kind="reminder", body=f"Reminder: {body['description']}"))
        await db.commit()


async def wait_for_queue(sqs, queue: str, max_wait_s: float = 120) -> str:
    """Queues may not exist yet at startup (e.g. LocalStack's init script still running): retry, don't crash."""
    delay, waited = 1.0, 0.0
    while True:
        try:
            return await queue_url(sqs, queue)
        except Exception as e:  # QueueDoesNotExist, connection refused, ...
            if waited >= max_wait_s:
                raise
            log.info("queue %s not ready (%s); retrying in %.0fs", queue, type(e).__name__, delay)
            await asyncio.sleep(delay)
            waited += delay
            delay = min(delay * 2, 10)


async def consume(queue: str) -> None:
    async with client("sqs") as sqs:
        url = await wait_for_queue(sqs, queue)
        log.info("consuming %s", queue)
        while True:
            resp = await sqs.receive_message(QueueUrl=url, MaxNumberOfMessages=10, WaitTimeSeconds=10)
            for m in resp.get("Messages", []):
                try:
                    await handle(json.loads(m["Body"]))
                    await sqs.delete_message(QueueUrl=url, ReceiptHandle=m["ReceiptHandle"])
                except Exception:  # leave it; SQS redelivers and eventually moves it to the DLQ
                    log.exception("failed to handle message")


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    s = get_settings()
    await asyncio.gather(consume(s.order_events_queue), consume(s.reminders_queue))


if __name__ == "__main__":
    asyncio.run(main())
