"""Retrieval eval: ablates each search stage on labelled queries.

Ground truth is derived from the structured catalog (subcategory / base product / dietary tags),
plus hand-written paraphrase queries where keyword overlap is low.

    uv run python -m evals.run_retrieval
"""

import asyncio
import json
import math
import statistics
from pathlib import Path

from app.db import SessionLocal
from app.domain.catalog import expand
from app.search.engine import Filters, search_engine
from evals.run_agent import pct

K = 10
MODES = ["bm25", "dense", "hybrid", "rerank", "full"]

# (query, relevance rule). Rules: ("sub", name) | ("base", [names]) | ("sub+tag", sub, tag) | ("tag", tag, category)
QUERIES: list[tuple[str, tuple]] = [
    # --- colloquial / Hinglish subcategory queries
    ("doodh", ("sub", "Milk")), ("dahi", ("sub", "Curd & Yogurt")), ("anda", ("sub", "Eggs")),
    ("chawal", ("sub", "Rice")), ("namkeen", ("sub", "Namkeen")), ("chai patti", ("sub", "Tea")),
    ("cold drink", ("sub", "Soft Drinks")), ("biscuits", ("sub", "Biscuits & Cookies")),
    ("instant noodles", ("base", ["Masala Instant Noodles", "Atta Noodles", "Korean Spicy Ramen"])),
    ("ice cream", ("sub", "Ice Cream")), ("cooking oil", ("sub", "Edible Oils")), ("dal", ("sub", "Dals & Pulses")),
    ("masala", ("sub", "Masalas & Spices")), ("bread", ("sub", "Bread")), ("chocolates", ("sub", "Chocolates")),
    ("fresh fruits", ("sub", "Fresh Fruits")), ("sabzi", ("sub", "Fresh Vegetables")), ("juice", ("sub", "Juices")),
    ("cereal", ("sub", "Cereals")), ("shampoo", ("sub", "Hair Care")), ("diapers", ("sub", "Diapers")),
    ("chicken", ("sub", "Chicken")), ("fish", ("sub", "Fish & Seafood")), ("paneer", ("base", ["Fresh Paneer", "Low Fat Paneer"])),
    ("ghee", ("base", ["Cow Ghee"])), ("atta", ("base", ["Whole Wheat Atta", "Multigrain Atta"])),
    # --- paraphrases with little lexical overlap
    ("bread without maida", ("base", ["Zero Maida 100% Whole Wheat Bread", "Brown Bread", "Multigrain Bread"])),
    ("something for pav bhaji", ("base", ["Ladi Pav"])),
    ("protein for the gym", ("base", ["Whey Protein Chocolate", "Protein Bar Peanut Butter"])),
    ("milk for someone lactose intolerant", ("base", ["Lactose Free Milk", "Almond Milk Unsweetened", "Soy Milk"])),
    ("fizzy drink without sugar", ("base", ["Diet Cola Zero Sugar"])),
    ("stuff to clean the floor", ("base", ["Floor Cleaner"])),
    ("wash dishes", ("base", ["Dishwash Liquid Gel", "Dishwash Bar"])),
    ("clothes washing powder", ("base", ["Detergent Powder"])),
    ("pizza cheese", ("base", ["Mozzarella Cheese"])),
    ("pasta sauce", ("base", ["Pizza Pasta Sauce"])),
    ("hydrating drink after workout", ("base", ["Coconut Water", "Energy Drink"])),
    ("crunchy spread for toast", ("base", ["Peanut Butter Crunchy"])),
    ("sweet spread for kids breakfast", ("base", ["Hazelnut Cocoa Spread", "Mixed Fruit Jam", "Pure Honey"])),
    ("frozen potato snacks", ("base", ["French Fries", "Aloo Tikki"])),
    ("tissue paper", ("base", ["Toilet Tissue Roll", "Kitchen Towel Roll"])),
    ("kids nutrition drink", ("base", ["Chocolate Health Drink"])),
    ("baby food", ("base", ["Baby Cereal Wheat Apple"])),
    ("leafy greens", ("base", ["Spinach (Palak)", "Coriander Leaves"])),
    ("caffeine boost", ("base", ["Instant Coffee", "Filter Coffee Powder", "Energy Drink"])),
    ("salty snacks for a party", ("sub", "Chips & Crisps")),
    ("chips that are not fried", ("base", ["Baked Ragi Chips", "Roasted Makhana Peri Peri"])),
    ("sunscreen", ("base", ["Sunscreen SPF 50"])),
    ("dandruff", ("base", ["Anti Dandruff Shampoo"])),
    ("teeth cleaning", ("sub", "Oral Care")),
    ("soap", ("base", ["Moisturising Bathing Bar", "Body Wash"])),
    ("korean noodles", ("base", ["Korean Spicy Ramen"])),
    ("ready meal to heat and eat", ("sub", "Ready to Eat")),
    ("muesli", ("base", ["No Added Sugar Muesli"])),
    ("oats", ("sub", "Oats & Muesli")),
    # --- dietary / attribute-constrained
    ("sugar free snacks", ("tag", "sugar-free", "Snacks")), ("sugar free drinks", ("tag", "sugar-free", "Beverages")),
    ("high protein dairy", ("tag", "high-protein", "Dairy & Eggs")), ("vegan milk", ("sub+tag", "Plant Milk", "vegan")),
    ("gluten free breakfast", ("tag", "gluten-free", "Breakfast")), ("organic dal", ("sub+tag", "Dals & Pulses", "organic")),
    ("organic rice", ("sub+tag", "Rice", "organic")), ("low fat milk", ("sub+tag", "Milk", "low-fat")),
    ("whole grain bread", ("sub+tag", "Bread", "whole-grain")), ("high fiber breakfast", ("tag", "high-fiber", "Breakfast")),
    ("healthy chips", ("sub+tag", "Chips & Crisps", "low-fat")), ("organic fruits", ("sub+tag", "Fresh Fruits", "organic")),
    ("organic vegetables", ("sub+tag", "Fresh Vegetables", "organic")), ("diabetic friendly sweets", ("tag", "sugar-free", "Snacks")),
    ("lean meat", ("sub+tag", "Chicken", "low-fat")), ("gluten free flour", ("sub+tag", "Atta & Flours", "gluten-free")),
]


def relevant_ids(rule: tuple, products: list[dict], ids: dict[str, int]) -> set[int]:
    kind = rule[0]
    if kind == "sub":
        return {ids[p["name"]] for p in products if p["subcategory"] == rule[1]}
    if kind == "base":
        return {ids[p["name"]] for p in products if p["base"] in rule[1]}
    if kind == "sub+tag":
        return {ids[p["name"]] for p in products if p["subcategory"] == rule[1] and rule[2] in p["tags"]}
    if kind == "tag":
        return {ids[p["name"]] for p in products if rule[1] in p["tags"] and p["category"] == rule[2]}
    raise ValueError(rule)


def metrics(ranked: list[int], rel: set[int]) -> dict:
    top = ranked[:K]
    hits = [1 if r in rel else 0 for r in top]
    dcg = sum(h / math.log2(i + 2) for i, h in enumerate(hits))
    idcg = sum(1 / math.log2(i + 2) for i in range(min(len(rel), K)))
    mrr = next((1 / (i + 1) for i, h in enumerate(hits) if h), 0.0)
    return {"recall": sum(hits) / min(len(rel), K), "ndcg": dcg / idcg if idcg else 0, "mrr": mrr}


async def main() -> None:
    products = expand()
    async with SessionLocal() as db:
        await search_engine.build(db)
        await search_engine.warmup()
        ids = {d.name: d.id for d in search_engine.docs}
        rows, per_query = [], []
        for mode in MODES:
            agg, lat = {"recall": [], "ndcg": [], "mrr": []}, []
            search_engine._qcache.clear()  # latency includes embedding the query
            for q, rule in QUERIES:
                rel = relevant_ids(rule, products, ids)
                assert rel, (q, rule)
                res = await search_engine.search(db, q, Filters(in_stock_only=False), k=K, mode=mode)
                m = metrics([h.product_id for h in res.hits], rel)
                for k2 in agg:
                    agg[k2].append(m[k2])
                lat.append(res.timings_ms["total"])
                per_query.append({"mode": mode, "query": q, **{k2: round(v, 3) for k2, v in m.items()}})
            rows.append({"mode": mode, "recall@10": round(statistics.mean(agg["recall"]), 3),
                         "ndcg@10": round(statistics.mean(agg["ndcg"]), 3), "mrr@10": round(statistics.mean(agg["mrr"]), 3),
                         "p50_ms": pct(lat, 0.5), "p95_ms": pct(lat, 0.95)})
            print(rows[-1], flush=True)
    out = {"queries": len(QUERIES), "k": K, "modes": rows, "per_query": per_query}
    path = Path(__file__).parent / "results" / "retrieval.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(out, indent=1))
    print(f"wrote {path}")


if __name__ == "__main__":
    asyncio.run(main())
