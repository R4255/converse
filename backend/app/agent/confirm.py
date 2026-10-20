"""Executes (or rejects) actions the agent proposed but the user had to approve.
This path never involves the LLM: approval is a deterministic, auditable click."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.protocol import ConfirmRequest, UIAction
from app.config import now
from app.domain import services as svc
from app.domain.models import PendingAction


async def resolve_pending(db: AsyncSession, req: ConfirmRequest) -> dict:
    pa = await db.get(PendingAction, req.token)
    if not pa or pa.user_id != req.user_id or pa.session_id != req.session_id:
        raise svc.DomainError("not_found", "this confirmation is not valid")
    if pa.status != "pending":
        raise svc.DomainError("already_handled", f"this action was already {pa.status}")
    if pa.expires_at < now():
        pa.status = "expired"
        await db.commit()
        raise svc.DomainError("expired", "this confirmation expired, please ask again")

    if not req.approve:
        pa.status = "rejected"
        await db.commit()
        return {"status": "rejected", "reply": "Okay, I won't do that.", "ui_actions": []}

    actions: list[UIAction] = []
    if pa.action == "place_order":
        cart = await svc.get_cart(db, req.user_id)
        if abs(cart["total"] - pa.payload["expected_total"]) > 0.01:
            # Cart changed between proposal and approval: never charge a different amount silently.
            pa.status = "stale"
            await db.commit()
            raise svc.DomainError("stale", f"your cart changed (now ₹{cart['total']}); please confirm again")
        out = await svc.place_order(db, req.user_id, idempotency_key=f"confirm:{pa.token}")
        reply = f"Order #{out['order_id']} placed for ₹{out['total']}."
        actions += [UIAction(type="navigate", payload={"path": f"/orders/{out['order_id']}"}),
                    UIAction(type="toast", payload={"level": "success", "text": reply})]
    elif pa.action == "clear_cart":
        await svc.clear_cart(db, req.user_id)
        reply = "Cart emptied."
    elif pa.action == "add_large_quantity":
        for line in pa.payload["lines"]:
            await svc.add_to_cart(db, req.user_id, line["variant_id"], line["quantity"],
                                  idempotency_key=f"confirm:{pa.token}:{line['variant_id']}")
        reply = "Added to your cart."
    else:
        raise svc.DomainError("invalid", f"unknown action {pa.action}")

    pa = await db.get(PendingAction, req.token)
    pa.status = "approved"
    await db.commit()
    cart = await svc.get_cart(db, req.user_id)
    actions.append(UIAction(type="refresh_cart", payload={"cart": cart}))
    return {"status": "approved", "reply": reply, "ui_actions": [a.model_dump() for a in actions]}
