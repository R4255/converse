"""Domain services. The REST API and the agent's tools both go through here."""

import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings, now
from app.domain.models import (
    CartItem,
    DeliveryPreference,
    IdempotencyKey,
    Notification,
    Order,
    OrderItem,
    OutboxEvent,
    Product,
    Reminder,
    Review,
    User,
    Variant,
)

FREE_DELIVERY_ABOVE = 199
DELIVERY_FEE = 25
HANDLING_FEE = 5


class DomainError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# ---------------- serialization ----------------
def variant_dict(v: Variant) -> dict:
    return {"variant_id": v.id, "label": v.label, "price": float(v.price), "mrp": float(v.mrp),
            "in_stock": v.stock > 0}


def product_dict(p: Product, brief: bool = False) -> dict:
    d = {"product_id": p.id, "name": p.name, "brand": p.brand, "category": p.category,
         "subcategory": p.subcategory, "rating": p.rating, "rating_count": p.rating_count,
         "tags": p.tags, "variants": [variant_dict(v) for v in p.variants]}
    if not brief:
        d["description"] = p.description
    return d


def order_dict(o: Order, with_items: bool = True) -> dict:
    tz = ZoneInfo(get_settings().timezone)
    d = {"order_id": o.id, "placed_at": o.placed_at.astimezone(tz).isoformat(timespec="minutes"),
         "status": o.status, "total": float(o.total), "item_count": len(o.items),
         "delivery": _slot_label(o.delivery_slot) if o.delivery_slot else None}
    if with_items:
        d["items"] = [{"variant_id": i.variant_id, "name": i.product_name, "label": i.variant_label,
                       "price": float(i.price), "quantity": i.quantity} for i in o.items]
    else:
        d["summary"] = ", ".join(i.product_name for i in o.items[:3]) + (
            f" +{len(o.items) - 3} more" if len(o.items) > 3 else "")
    return d


# ---------------- catalog ----------------
async def get_user(db: AsyncSession, user_id: int) -> User:
    u = await db.get(User, user_id)
    if not u:
        raise DomainError("not_found", f"user {user_id} not found")
    return u


async def get_product(db: AsyncSession, product_id: int) -> Product:
    p = await db.get(Product, product_id)
    if not p:
        raise DomainError("not_found", f"product {product_id} not found")
    return p


async def get_variant(db: AsyncSession, variant_id: int) -> Variant:
    v = await db.get(Variant, variant_id)
    if not v:
        raise DomainError("not_found", f"variant {variant_id} not found")
    return v


async def product_reviews(db: AsyncSession, product_id: int, limit: int = 5) -> list[dict]:
    rows = (await db.execute(select(Review).where(Review.product_id == product_id).limit(limit))).scalars()
    return [{"author": r.author, "rating": r.rating, "body": r.body} for r in rows]


async def categories(db: AsyncSession) -> list[dict]:
    rows = await db.execute(
        select(Product.category, Product.subcategory, func.count()).group_by(Product.category, Product.subcategory)
        .order_by(Product.category, Product.subcategory))
    out: dict[str, dict] = {}
    for cat, sub, n in rows:
        out.setdefault(cat, {"name": cat, "subcategories": [], "count": 0})
        out[cat]["subcategories"].append(sub)
        out[cat]["count"] += n
    return list(out.values())


# ---------------- cart ----------------
async def get_cart(db: AsyncSession, user_id: int) -> dict:
    items = (await db.execute(
        select(CartItem).where(CartItem.user_id == user_id).order_by(CartItem.added_at))).scalars().all()
    lines = [{"variant_id": c.variant_id, "product_id": c.variant.product_id, "name": c.variant.product.name,
              "label": c.variant.label, "price": float(c.variant.price), "quantity": c.quantity,
              "line_total": round(float(c.variant.price) * c.quantity, 2)} for c in items]
    slot = await get_delivery_slot(db, user_id)
    subtotal = round(sum(line["line_total"] for line in lines), 2)
    delivery = 0 if subtotal >= FREE_DELIVERY_ABOVE or not lines else DELIVERY_FEE
    handling = HANDLING_FEE if lines else 0
    return {"items": lines, "item_count": sum(line["quantity"] for line in lines), "subtotal": subtotal,
            "delivery_fee": delivery, "handling_fee": handling, "total": round(subtotal + delivery + handling, 2),
            "free_delivery_gap": max(0, round(FREE_DELIVERY_ABOVE - subtotal, 2)) if lines else 0,
            "delivery_slot": slot.isoformat() if slot else None, "delivery_label": _slot_label(slot)}


def _slot_label(slot: datetime | None) -> str:
    from app.agent.resolvers import slot_label

    return slot_label(slot.astimezone(ZoneInfo(get_settings().timezone)) if slot else None)


async def get_delivery_slot(db: AsyncSession, user_id: int) -> datetime | None:
    pref = await db.get(DeliveryPreference, user_id)
    if pref and pref.slot_start > now():
        return pref.slot_start
    return None


async def set_delivery_slot(db: AsyncSession, user_id: int, start: datetime | None) -> dict:
    """start=None selects express delivery."""
    pref = await db.get(DeliveryPreference, user_id)
    if start is None:
        if pref:
            await db.delete(pref)
    elif pref:
        pref.slot_start = start
    else:
        db.add(DeliveryPreference(user_id=user_id, slot_start=start))
    await db.commit()
    return {"delivery": _slot_label(start)}


async def _idempotent(db: AsyncSession, key: str | None, user_id: int):
    if not key:
        return None
    row = await db.get(IdempotencyKey, key)
    return row.result if row else None


async def _remember(db: AsyncSession, key: str | None, user_id: int, result: dict) -> None:
    if key:
        db.add(IdempotencyKey(key=key, user_id=user_id, result=result))


async def add_to_cart(db: AsyncSession, user_id: int, variant_id: int, quantity: int = 1,
                      idempotency_key: str | None = None) -> dict:
    if (cached := await _idempotent(db, idempotency_key, user_id)) is not None:
        return {**cached, "replayed": True}
    if quantity < 1:
        raise DomainError("invalid", "quantity must be >= 1")
    v = await get_variant(db, variant_id)
    if v.stock <= 0:
        raise DomainError("out_of_stock", f"{v.product.name} ({v.label}) is out of stock")
    item = await db.get(CartItem, (user_id, variant_id))
    if item:
        item.quantity += quantity
    else:
        item = CartItem(user_id=user_id, variant_id=variant_id, quantity=quantity)
        db.add(item)
    if item.quantity > v.stock:
        raise DomainError("insufficient_stock", f"only {v.stock} of {v.product.name} ({v.label}) available")
    result = {"added": {"variant_id": v.id, "name": v.product.name, "label": v.label, "quantity": quantity},
              "cart_quantity": item.quantity}
    await _remember(db, idempotency_key, user_id, result)
    await db.commit()
    return result


async def set_cart_quantity(db: AsyncSession, user_id: int, variant_id: int, quantity: int) -> dict:
    item = await db.get(CartItem, (user_id, variant_id))
    if not item:
        raise DomainError("not_in_cart", f"variant {variant_id} is not in the cart")
    name = f"{item.variant.product.name} ({item.variant.label})"
    if quantity <= 0:
        await db.delete(item)
    else:
        if quantity > item.variant.stock:
            raise DomainError("insufficient_stock", f"only {item.variant.stock} available")
        item.quantity = quantity
    await db.commit()
    return {"updated": name, "quantity": max(quantity, 0)}


async def clear_cart(db: AsyncSession, user_id: int) -> dict:
    res = await db.execute(delete(CartItem).where(CartItem.user_id == user_id))
    await db.commit()
    return {"removed_lines": res.rowcount}


# ---------------- orders ----------------
async def list_orders(db: AsyncSession, user_id: int, date_from: date | None = None, date_to: date | None = None,
                      contains: str | None = None, limit: int = 10) -> list[Order]:
    tz = ZoneInfo(get_settings().timezone)
    q = select(Order).where(Order.user_id == user_id)
    if date_from:
        q = q.where(Order.placed_at >= datetime.combine(date_from, time.min, tz))
    if date_to:
        q = q.where(Order.placed_at < datetime.combine(date_to + timedelta(days=1), time.min, tz))
    if contains:
        sub = select(OrderItem.order_id).where(OrderItem.product_name.ilike(f"%{contains}%"))
        q = q.where(Order.id.in_(sub))
    q = q.order_by(Order.placed_at.desc()).limit(limit)
    return list((await db.execute(q)).scalars().all())


async def get_order(db: AsyncSession, user_id: int, order_id: int) -> Order:
    o = await db.get(Order, order_id)
    if not o or o.user_id != user_id:
        raise DomainError("not_found", f"order {order_id} not found")
    return o


async def reorder(db: AsyncSession, user_id: int, order_id: int, idempotency_key: str | None = None) -> dict:
    if (cached := await _idempotent(db, idempotency_key, user_id)) is not None:
        return {**cached, "replayed": True}
    o = await get_order(db, user_id, order_id)
    added, skipped = [], []
    for it in o.items:
        v = await db.get(Variant, it.variant_id)
        if not v or v.stock <= 0:
            skipped.append(f"{it.product_name} ({it.variant_label})")
            continue
        item = await db.get(CartItem, (user_id, v.id))
        if item:
            item.quantity += it.quantity
        else:
            db.add(CartItem(user_id=user_id, variant_id=v.id, quantity=it.quantity))
        added.append(f"{it.quantity} x {it.product_name} ({it.variant_label})")
    result = {"order_id": order_id, "added": added, "skipped_out_of_stock": skipped}
    await _remember(db, idempotency_key, user_id, result)
    await db.commit()
    return result


async def place_order(db: AsyncSession, user_id: int, idempotency_key: str | None = None) -> dict:
    """Atomically: lock stock, create order, clear cart, and enqueue an outbox event."""
    if (cached := await _idempotent(db, idempotency_key, user_id)) is not None:
        return {**cached, "replayed": True}
    user = await get_user(db, user_id)
    cart = (await db.execute(select(CartItem).where(CartItem.user_id == user_id))).scalars().all()
    if not cart:
        raise DomainError("empty_cart", "cart is empty")
    variant_ids = [c.variant_id for c in cart]
    locked = {v.id: v for v in (await db.execute(
        select(Variant).where(Variant.id.in_(variant_ids)).with_for_update(of=Variant))).scalars()}
    items = []
    for c in cart:
        v = locked[c.variant_id]
        if v.stock < c.quantity:
            raise DomainError("insufficient_stock", f"{v.product.name} ({v.label}): only {v.stock} left")
        v.stock -= c.quantity
        items.append(OrderItem(variant_id=v.id, product_name=v.product.name, variant_label=v.label,
                               price=v.price, quantity=c.quantity))
    summary = await get_cart(db, user_id)
    slot = await get_delivery_slot(db, user_id)
    order = Order(user_id=user_id, status="scheduled" if slot else "placed", placed_at=now(), total=summary["total"],
                  address=user.address, items=items, delivery_slot=slot)
    db.add(order)
    await db.execute(delete(CartItem).where(CartItem.user_id == user_id))
    await db.execute(delete(DeliveryPreference).where(DeliveryPreference.user_id == user_id))
    await db.flush()
    event_id = str(uuid.uuid4())
    db.add(OutboxEvent(id=event_id, queue=get_settings().order_events_queue, body={
        "event_id": event_id, "type": "order.placed", "order_id": order.id, "user_id": user_id,
        "total": float(order.total),
        "items": [{"product_id": locked[i.variant_id].product_id, "quantity": i.quantity} for i in items]}))
    result = {"order_id": order.id, "total": float(order.total), "status": order.status,
              "delivery": _slot_label(slot)}
    await _remember(db, idempotency_key, user_id, result)
    await db.commit()
    return result


# ---------------- reminders & notifications ----------------
DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]


def cron_for(frequency: str, day_of_week: str | None, hour: int, minute: int = 0) -> str:
    """Build an EventBridge cron deterministically (the LLM never writes cron syntax)."""
    if frequency == "daily":
        return f"cron({minute} {hour} * * ? *)"
    if frequency == "weekly":
        d = (day_of_week or "SUN")[:3].upper()
        if d not in DAYS:
            raise DomainError("invalid", f"unknown day {day_of_week}")
        return f"cron({minute} {hour} ? * {d} *)"
    if frequency == "monthly":
        return f"cron({minute} {hour} 1 * ? *)"
    raise DomainError("invalid", f"unsupported frequency {frequency}")


async def create_reminder(db: AsyncSession, user_id: int, description: str, frequency: str,
                          day_of_week: str | None, hour: int, variant_ids: list[int]) -> dict:
    from app.aws.clients import create_schedule

    expr = cron_for(frequency, day_of_week, hour)
    r = Reminder(user_id=user_id, description=description, schedule_expression=expr, items=variant_ids)
    db.add(r)
    await db.flush()
    try:
        r.schedule_arn = await create_schedule(
            f"reminder-{user_id}-{r.id}", expr,
            {"type": "reminder.due", "reminder_id": r.id, "user_id": user_id, "description": description},
            get_settings().timezone)
    except Exception as e:  # keep the reminder even if the scheduler is unavailable
        r.schedule_arn = None
        await db.commit()
        return {"reminder_id": r.id, "schedule": expr, "scheduled": False, "warning": str(e)[:200]}
    await db.commit()
    return {"reminder_id": r.id, "schedule": expr, "scheduled": True, "description": description}


async def list_reminders(db: AsyncSession, user_id: int) -> list[dict]:
    rows = (await db.execute(select(Reminder).where(Reminder.user_id == user_id).order_by(Reminder.id))).scalars()
    return [{"reminder_id": r.id, "description": r.description, "schedule": r.schedule_expression,
             "items": r.items, "scheduled": bool(r.schedule_arn)} for r in rows]


async def list_notifications(db: AsyncSession, user_id: int, limit: int = 20) -> list[dict]:
    rows = (await db.execute(select(Notification).where(Notification.user_id == user_id)
                             .order_by(Notification.id.desc()).limit(limit))).scalars()
    return [{"id": n.id, "kind": n.kind, "body": n.body, "created_at": n.created_at.isoformat()} for n in rows]
