"""Regression table for product resolution (seeded DB + embeddings). Each row came from a real failure."""

import pytest

from app.agent.resolvers import purchase_counts, resolve_product
from app.db import SessionLocal
from app.search.engine import search_engine

pytestmark = pytest.mark.asyncio(loop_scope="session")

CASES = [
    # (user, reference, expected product name prefix | "ASK", expected size label)
    (1, "cheese", "ASK", None), (1, "oil", "ASK", None), (1, "chocolate", "ASK", None),
    (1, "detergent", "ASK", None), (1, "namkeen", "ASK", None), (1, "rice", "ASK", None),
    (1, "milk", "Amul Toned Milk", "1 L"),                 # habit
    (1, "eggs", "Eggoz Brown Eggs", None), (1, "anda", "Eggoz Brown Eggs", None),  # Hinglish
    (2, "paneer", "Amul Fresh Paneer", None),
    (1, "tata salt", "Tata Iodised Salt", None),           # brand given -> most popular match
    (1, "coke 2 litre", "Coca-Cola Cola", "2 L"),          # alias + size
    (1, "30 eggs", None, "Pack of 30"),                    # count -> pack size
    (1, "big pack of fortune sunflower oil", "Fortune Sunflower Oil", "5 L"),
    (1, "5 kg pack of aashirvaad atta", "Aashirvaad Whole Wheat Atta", "5 kg"),
    (3, "soya milk", "Sofit Soy Milk", None),
    (2, "tomoato", "FarmFresh Tomato", None),              # typo
]


@pytest.mark.parametrize("user,ref,name,label", CASES)
async def test_resolution(user, ref, name, label):
    async with SessionLocal() as db:
        if not search_engine.docs:
            await search_engine.build(db)
        r = await resolve_product(db, ref, None, [], await purchase_counts(db, user))
    if name == "ASK":
        assert r.status == "ambiguous" and len(r.options) >= 2, (r.status, r.product and r.product.name)
        return
    assert r.status == "ok", (r.status, r.reason)
    if name:
        assert r.product.name.startswith(name), r.product.name
    if label:
        assert r.variant.label == label, r.variant.label
