import asyncio
import contextlib
import json
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.agent.confirm import resolve_pending
from app.agent.loop import Agent, collect
from app.agent.protocol import ConfirmRequest, TurnRequest
from app.db import SessionLocal, get_db, init_db
from app.domain import services as svc
from app.domain.models import AgentTrace, Product
from app.outbox import relay_forever
from app.search.engine import Filters, search_engine

log = logging.getLogger("converse")
agent = Agent()


async def _refresh_signals_forever() -> None:
    """The worker updates popularity from order events; refresh the in-memory ranking signals."""
    while True:
        await asyncio.sleep(60)
        with contextlib.suppress(Exception):
            async with SessionLocal() as db:
                rows = await db.execute(select(Product.id, Product.popularity))
                for pid, pop in rows:
                    if d := search_engine.by_id.get(pid):
                        d.popularity = pop


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    async with SessionLocal() as db:
        await search_engine.build(db)
    await search_engine.warmup()
    await agent.llm.warmup()
    tasks = [asyncio.create_task(relay_forever()), asyncio.create_task(_refresh_signals_forever())]
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="Converse API", version="1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(svc.DomainError)
async def domain_error(_, e: svc.DomainError):
    from fastapi.responses import JSONResponse

    status = {"not_found": 404, "expired": 410, "stale": 409, "already_handled": 409}.get(e.code, 400)
    return JSONResponse({"error": e.code, "message": e.message}, status_code=status)


def uid(x_user_id: int = Header(1)) -> int:
    return x_user_id


# ---------------- catalog ----------------
@app.get("/api/health")
async def health(db: AsyncSession = Depends(get_db)):
    await db.execute(text("select 1"))
    return {"ok": True, "products_indexed": len(search_engine.docs), "model": agent.llm.model}


@app.get("/api/me")
async def me(user_id: int = Depends(uid), db: AsyncSession = Depends(get_db)):
    u = await svc.get_user(db, user_id)
    return {"id": u.id, "name": u.name, "email": u.email, "address": u.address}


@app.get("/api/categories")
async def categories(db: AsyncSession = Depends(get_db)):
    return await svc.categories(db)


@app.get("/api/products")
async def products(q: str | None = None, category: str | None = None, sort: str = "relevance",
                   limit: int = Query(24, le=60), db: AsyncSession = Depends(get_db)):
    if q:
        res = await search_engine.search(db, q, Filters(category=category, in_stock_only=False), k=limit, sort=sort)
        ids, timings = [h.product_id for h in res.hits], res.timings_ms
    else:
        stmt = select(Product.id).order_by(Product.popularity.desc()).limit(limit)
        if category:
            stmt = select(Product.id).where((Product.category == category) | (Product.subcategory == category)) \
                .order_by(Product.popularity.desc()).limit(limit)
        ids, timings = list((await db.execute(stmt)).scalars()), {}
    items = [svc.product_dict(await db.get(Product, i), brief=True) for i in ids]
    return {"items": items, "timings_ms": timings}


@app.get("/api/products/{product_id}")
async def product(product_id: int, db: AsyncSession = Depends(get_db)):
    p = await svc.get_product(db, product_id)
    return {**svc.product_dict(p), "reviews": await svc.product_reviews(db, product_id, limit=10)}


# ---------------- cart & orders ----------------
class AddItem(BaseModel):
    variant_id: int
    quantity: int = Field(1, ge=1, le=99)


class SetQty(BaseModel):
    quantity: int = Field(ge=0, le=99)


@app.get("/api/cart")
async def cart(user_id: int = Depends(uid), db: AsyncSession = Depends(get_db)):
    return await svc.get_cart(db, user_id)


@app.post("/api/cart/items")
async def add_item(body: AddItem, user_id: int = Depends(uid), idempotency_key: str | None = Header(None),
                   db: AsyncSession = Depends(get_db)):
    await svc.add_to_cart(db, user_id, body.variant_id, body.quantity, idempotency_key)
    return await svc.get_cart(db, user_id)


@app.patch("/api/cart/items/{variant_id}")
async def set_qty(variant_id: int, body: SetQty, user_id: int = Depends(uid), db: AsyncSession = Depends(get_db)):
    await svc.set_cart_quantity(db, user_id, variant_id, body.quantity)
    return await svc.get_cart(db, user_id)


@app.delete("/api/cart")
async def empty_cart(user_id: int = Depends(uid), db: AsyncSession = Depends(get_db)):
    await svc.clear_cart(db, user_id)
    return await svc.get_cart(db, user_id)


@app.get("/api/orders")
async def orders(limit: int = Query(30, le=100), user_id: int = Depends(uid), db: AsyncSession = Depends(get_db)):
    return [svc.order_dict(o, with_items=False) for o in await svc.list_orders(db, user_id, limit=limit)]


@app.get("/api/orders/{order_id}")
async def order(order_id: int, user_id: int = Depends(uid), db: AsyncSession = Depends(get_db)):
    return svc.order_dict(await svc.get_order(db, user_id, order_id))


@app.post("/api/orders")
async def place(user_id: int = Depends(uid), idempotency_key: str = Header(...), db: AsyncSession = Depends(get_db)):
    """Manual checkout button. Requires an Idempotency-Key so double-clicks never double-order."""
    return await svc.place_order(db, user_id, idempotency_key=f"manual:{user_id}:{idempotency_key}")


@app.get("/api/reminders")
async def reminders(user_id: int = Depends(uid), db: AsyncSession = Depends(get_db)):
    return await svc.list_reminders(db, user_id)


@app.get("/api/notifications")
async def notifications(user_id: int = Depends(uid), db: AsyncSession = Depends(get_db)):
    return await svc.list_notifications(db, user_id)


# ---------------- agent ----------------
@app.post("/api/agent/turn")
async def agent_turn(req: TurnRequest, user_id: int = Depends(uid)):
    """Server-sent events: step / ui_action / message / done."""
    req.user_id = user_id

    async def stream():
        async with SessionLocal() as db:
            async for ev in agent.run(db, req):
                if ev.type == "done":
                    ev.data.pop("steps", None)
                yield {"event": ev.type, "data": json.dumps(ev.data, default=str)}

    return EventSourceResponse(stream())


@app.post("/api/agent/turn/sync")
async def agent_turn_sync(req: TurnRequest, user_id: int = Depends(uid), db: AsyncSession = Depends(get_db)):
    req.user_id = user_id
    return await collect(agent, db, req)


@app.post("/api/agent/confirm")
async def agent_confirm(req: ConfirmRequest, user_id: int = Depends(uid), db: AsyncSession = Depends(get_db)):
    req.user_id = user_id
    return await resolve_pending(db, req)


@app.get("/api/traces")
async def traces(limit: int = Query(50, le=200), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(AgentTrace).order_by(AgentTrace.created_at.desc()).limit(limit))).scalars()
    return [{"id": t.id, "input": t.input, "reply": t.reply, "route": t.route, "model": t.model,
             "latency_ms": t.latency_ms, "prompt_tokens": t.prompt_tokens, "completion_tokens": t.completion_tokens,
             "tools": [s.get("tool") for s in t.steps if s.get("kind") == "tool"], "error": t.error,
             "created_at": t.created_at.isoformat()} for t in rows]


@app.get("/api/traces/{trace_id}")
async def trace(trace_id: str, db: AsyncSession = Depends(get_db)):
    t = await db.get(AgentTrace, trace_id)
    if not t:
        raise HTTPException(404)
    return {"id": t.id, "input": t.input, "reply": t.reply, "route": t.route, "model": t.model, "steps": t.steps,
            "ui_actions": t.ui_actions, "latency_ms": t.latency_ms, "prompt_tokens": t.prompt_tokens,
            "completion_tokens": t.completion_tokens, "error": t.error, "created_at": t.created_at.isoformat()}


@app.get("/api/evals")
async def evals():
    """Latest eval summaries (written by evals/run_agent.py and evals/run_retrieval.py)."""
    from pathlib import Path

    def load() -> dict:
        root = Path(__file__).resolve().parent.parent / "evals" / "results"
        runs = [json.loads(p.read_text())["summary"] for p in sorted(root.glob("agent-*.json"))]
        runs.sort(key=lambda s: -s["task_success"])
        retrieval = root / "retrieval.json"
        if not retrieval.exists():
            return {"agent": runs, "retrieval": None}
        r = json.loads(retrieval.read_text())
        r.pop("per_query", None)
        return {"agent": runs, "retrieval": r}

    return await asyncio.to_thread(load)


class SlotChoice(BaseModel):
    start: str | None = None  # ISO datetime, or null for express


@app.get("/api/delivery-slots")
async def delivery_slots():
    """Bookable 1-hour slots for today and the next two days."""
    from datetime import timedelta

    from app.agent.resolvers import SLOT_LAST_HOUR, earliest_slot, slot_label
    from app.config import now

    t, out = earliest_slot(now()), []
    end = (now() + timedelta(days=2)).replace(hour=SLOT_LAST_HOUR, minute=0, second=0, microsecond=0)
    while t <= end:
        if t.hour <= SLOT_LAST_HOUR:
            out.append({"start": t.isoformat(), "label": slot_label(t)})
        t += timedelta(hours=1)
    return [{"start": None, "label": slot_label(None)}, *out]


@app.put("/api/delivery-slot")
async def choose_slot(body: SlotChoice, user_id: int = Depends(uid), db: AsyncSession = Depends(get_db)):
    from datetime import datetime

    await svc.set_delivery_slot(db, user_id, datetime.fromisoformat(body.start) if body.start else None)
    return await svc.get_cart(db, user_id)
