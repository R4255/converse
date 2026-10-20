"""Integration tests against the seeded Postgres (podman-compose up postgres). No LLM needed."""

import uuid

import pytest
from sqlalchemy import delete, select

from app.agent.confirm import resolve_pending
from app.agent.protocol import ConfirmRequest
from app.agent.tools import TOOLS, ToolContext
from app.db import SessionLocal
from app.domain import services as svc
from app.domain.models import CartItem, Order, OrderItem, OutboxEvent, PendingAction, Product, Variant
from app.search.engine import search_engine

pytestmark = pytest.mark.asyncio(loop_scope="session")
USER = 3


@pytest.fixture(autouse=True)
async def clean_cart():
    async with SessionLocal() as db:
        if not search_engine.docs:
            await search_engine.build(db)
        await db.execute(delete(CartItem).where(CartItem.user_id == USER))
        await db.commit()
    yield
    async with SessionLocal() as db:
        await db.execute(delete(CartItem).where(CartItem.user_id == USER))
        await db.commit()


async def _variant(db, name: str) -> Variant:
    p = (await db.execute(select(Product).where(Product.name == name))).scalar_one()
    return next(v for v in p.variants if v.stock > 0)


async def test_add_to_cart_is_idempotent():
    async with SessionLocal() as db:
        v = await _variant(db, "Amul Toned Milk")
        key = f"test:{uuid.uuid4()}"
        await svc.add_to_cart(db, USER, v.id, 2, key)
        replay = await svc.add_to_cart(db, USER, v.id, 2, key)
        cart = await svc.get_cart(db, USER)
    assert replay["replayed"] is True
    assert cart["items"][0]["quantity"] == 2


async def test_place_order_decrements_stock_and_writes_outbox():
    async with SessionLocal() as db:
        v = await _variant(db, "Britannia Brown Bread")
        stock = v.stock
        await svc.add_to_cart(db, USER, v.id, 1)
        out = await svc.place_order(db, USER, idempotency_key=f"test:{uuid.uuid4()}")
    async with SessionLocal() as db:
        assert (await db.get(Variant, v.id)).stock == stock - 1
        events = (await db.execute(select(OutboxEvent))).scalars().all()
        assert any(e.body["order_id"] == out["order_id"] for e in events)
        assert (await svc.get_cart(db, USER))["items"] == []
        # keep fixtures stable for the evals
        await db.execute(delete(OutboxEvent).where(OutboxEvent.body["order_id"].as_integer() == out["order_id"]))
        await db.execute(delete(OrderItem).where(OrderItem.order_id == out["order_id"]))
        await db.execute(delete(Order).where(Order.id == out["order_id"]))
        await db.commit()


async def test_place_order_tool_only_creates_a_pending_action():
    async with SessionLocal() as db:
        v = await _variant(db, "Amul Toned Milk")
        await svc.add_to_cart(db, USER, v.id, 1)
        n_orders = len(await svc.list_orders(db, USER, limit=1000))
        ctx = ToolContext(db=db, user_id=USER, session_id="s-test", turn_id="t", memory={})
        res = await TOOLS["place_order"].handler(ctx, TOOLS["place_order"].args())
        assert res.data["status"] == "awaiting_user_confirmation"
        assert len(await svc.list_orders(db, USER, limit=1000)) == n_orders
        assert any(a.type == "confirm" for a in ctx.ui_actions)


async def test_confirm_rejects_if_cart_changed_after_proposal():
    async with SessionLocal() as db:
        v = await _variant(db, "Amul Toned Milk")
        await svc.add_to_cart(db, USER, v.id, 1)
        ctx = ToolContext(db=db, user_id=USER, session_id="s-stale", turn_id="t", memory={})
        await TOOLS["place_order"].handler(ctx, TOOLS["place_order"].args())
        token = next(a.payload["token"] for a in ctx.ui_actions if a.type == "confirm")
        await svc.add_to_cart(db, USER, v.id, 1)  # cart changes before the user clicks confirm
        with pytest.raises(svc.DomainError) as e:
            await resolve_pending(db, ConfirmRequest(user_id=USER, session_id="s-stale", token=token, approve=True))
        assert e.value.code == "stale"


async def test_confirm_token_is_bound_to_user_and_single_use():
    async with SessionLocal() as db:
        v = await _variant(db, "Amul Toned Milk")
        await svc.add_to_cart(db, USER, v.id, 1)
        ctx = ToolContext(db=db, user_id=USER, session_id="s-bind", turn_id="t", memory={})
        await TOOLS["clear_cart"].handler(ctx, TOOLS["clear_cart"].args())
        token = next(a.payload["token"] for a in ctx.ui_actions if a.type == "confirm")
        with pytest.raises(svc.DomainError):  # another user cannot use it
            await resolve_pending(db, ConfirmRequest(user_id=1, session_id="s-bind", token=token, approve=True))
        await resolve_pending(db, ConfirmRequest(user_id=USER, session_id="s-bind", token=token, approve=True))
        with pytest.raises(svc.DomainError) as e:  # and it cannot be replayed
            await resolve_pending(db, ConfirmRequest(user_id=USER, session_id="s-bind", token=token, approve=True))
        assert e.value.code == "already_handled"
        await db.execute(delete(PendingAction).where(PendingAction.session_id.in_(["s-bind", "s-stale", "s-test"])))
        await db.commit()


async def test_large_quantity_requires_confirmation():
    async with SessionLocal() as db:
        ctx = ToolContext(db=db, user_id=USER, session_id="s-big", turn_id="t", memory={})
        args = TOOLS["add_to_cart"].args.model_validate({"items": [{"product": "coca-cola cola", "quantity": 40}]})
        await TOOLS["add_to_cart"].handler(ctx, args)
        assert (await svc.get_cart(db, USER))["items"] == []
        assert any(a.type == "confirm" and a.payload["action"] == "add_large_quantity" for a in ctx.ui_actions)


async def test_review_injection_is_hidden_from_the_model():
    async with SessionLocal() as db:
        ctx = ToolContext(db=db, user_id=USER, session_id="s-inj", turn_id="t", memory={})
        args = TOOLS["get_product_details"].args(product="Lay's Classic Salted Potato Chips")
        res = await TOOLS["get_product_details"].handler(ctx, args)
        texts = " ".join(r["text"] for r in res.data["untrusted_reviews"]).lower()
        assert "ignore all previous instructions" not in texts and "hidden" in texts


async def test_habitual_variant_is_preferred():
    from app.agent.resolvers import purchase_counts, resolve_product

    async with SessionLocal() as db:
        r = await resolve_product(db, "my usual milk", None, [], await purchase_counts(db, 1))
        assert r.status == "ok" and r.product.name == "Amul Toned Milk" and r.variant.label == "1 L"
