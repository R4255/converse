"""Synthetic tool-calling data for distilling the Converse agent into a small model.

Labels come from code (templates + catalog), not from a teacher model, so every target is correct by
construction. Prompts use the agent's exact system prompt, tool schemas and per-turn context format.
Anything too similar to an eval utterance is dropped so the eval stays held out.

    cd backend && uv run python ../finetune/gen_data.py
"""

import json
import random
import sys
from datetime import date, timedelta
from pathlib import Path

from rapidfuzz import fuzz

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.agent.loop import CONTEXT, SYSTEM  # noqa: E402
from app.agent.tools import tool_specs  # noqa: E402
from app.domain.catalog import expand  # noqa: E402
from evals.tasks import TASKS  # noqa: E402

OUT = Path(__file__).parent / "data"
rng = random.Random(1234)
TOOLS = tool_specs()
NAMES = ["Aditi", "Kabir", "Sana"]
EVAL_UTTERANCES = [t["say"].lower() for task in TASKS for t in task["turns"]]

CATALOG = expand()
# How users refer to products: generic words, base names, brand + base.
GENERIC = ["milk", "bread", "eggs", "atta", "rice", "sugar", "salt", "butter", "paneer", "curd", "onions", "potatoes",
           "tomatoes", "bananas", "apples", "chips", "biscuits", "tea", "coffee", "noodles", "ketchup", "ghee", "oats",
           "honey", "jam", "peanut butter", "toothpaste", "shampoo", "soap", "detergent", "coconut water", "dal"]
HINGLISH = ["doodh", "dahi", "anda", "aloo", "pyaz", "tamatar", "chawal", "cheeni", "namak", "chai", "palak"]
BASES = sorted({p["base"] for p in CATALOG})
BRANDED = [p["name"] for p in CATALOG]


def product_mention() -> str:
    r = rng.random()
    if r < 0.30:
        return rng.choice(GENERIC)
    if r < 0.38:
        return rng.choice(HINGLISH)
    if r < 0.70:
        return rng.choice(BASES).lower()
    return rng.choice(BRANDED).lower()


SIZES = ["1 litre", "500 ml", "2 litre", "1 kg", "5 kg", "500 g", "200 g", "pack of 6", "pack of 12", "big pack", "small pack"]
NUMS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}
DATES = ["12th march", "march 3", "3rd february", "14 august", "jan 20", "last tuesday", "last sunday", "yesterday",
         "on wednesday", "last month", "in july", "in may", "the day before yesterday", "last week", "last 15 days",
         "2nd september", "21st june", "this month"]
TIMES = ["tomorrow at 8 am", "this evening", "tonight", "tomorrow morning", "at 7 pm", "day after tomorrow at 6 pm",
         "tomorrow around 9", "by 8 pm today", "tomorrow afternoon", "asap", "tomorrow at 10:30 am", "early tomorrow"]
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
ORDINALS = [("first", 1), ("second", 2), ("third", 3), ("fourth", 4), ("last", None)]


def call(name: str, **args) -> dict:
    return {"type": "function", "function": {"name": name, "arguments": {k: v for k, v in args.items() if v is not None}}}


def qty_phrase(q: int) -> str:
    return rng.choice([str(q), NUMS.get(q, str(q))]) if q > 1 else rng.choice(["", "a", "one"])


def line(product: str, q: int = 1, size: str | None = None) -> dict:
    d = {"product": product, "quantity": q}
    if size:
        d["size"] = size
    return d


# ---------------- intent generators: return (user message, assistant tool calls or text, context) ----------------
def g_add_single():
    p, q = product_mention(), rng.choice([1, 1, 1, 2, 2, 3, 4])
    size = rng.choice(SIZES) if rng.random() < 0.25 else None
    qp = qty_phrase(q)
    tpl = rng.choice(["add {qp} {sz}{p}", "please add {qp} {sz}{p} to my cart", "put {qp} {sz}{p} in the basket",
                      "I want {qp} {sz}{p}", "can you add {qp} {sz}{p}", "get me {qp} {sz}{p}", "{p} x{q} please",
                      "throw {qp} {sz}{p} in my cart", "need {qp} {sz}{p}"])
    msg = tpl.format(qp=qp, q=q, p=p, sz=f"{size} " if size else "").replace("  ", " ").strip()
    return msg, [call("add_to_cart", items=[line(p, q, size)])], {}


def g_add_multi():
    ps = rng.sample(GENERIC + HINGLISH + [b.lower() for b in BASES], rng.choice([2, 2, 3]))
    qs = [rng.choice([1, 1, 2]) for _ in ps]
    parts = [f"{qty_phrase(q)} {p}".strip() for p, q in zip(ps, qs, strict=True)]
    joined = ", ".join(parts[:-1]) + " and " + parts[-1]
    msg = rng.choice(["add {j}", "put {j} in my cart", "I need {j}", "order {j} for me", "{j} please"]).format(j=joined)
    if msg.startswith("order"):  # "order X" is an add request, not a checkout request
        msg = msg.replace("order", "add", 1)
    return msg, [call("add_to_cart", items=[line(p, q) for p, q in zip(ps, qs, strict=True)])], {}


def g_usual():
    p = rng.choice(["milk", "bread", "eggs", "tea", "atta", "paneer", "curd", "bananas", "oats", "coffee"])
    phrase = rng.choice(["my usual {p}", "the regular {p}", "my normal {p}", "the {p} I always buy"]).format(p=p)
    msg = rng.choice(["add {x}", "{x} please", "get {x}", "put {x} in the cart"]).format(x=phrase)
    return msg, [call("add_to_cart", items=[line(phrase)])], {}


def g_add_then_checkout():
    p, q = product_mention(), rng.choice([1, 1, 2])
    msg = rng.choice(["add {qp} {p} and go to checkout", "put {qp} {p} in the cart then open checkout",
                      "{p} and then the checkout page", "add {qp} {p}, then take me to check out",
                      "grab {qp} {p} and head to checkout"]).format(qp=qty_phrase(q), p=p).replace("  ", " ")
    return msg, [call("add_to_cart", items=[line(p, q)]), call("navigate", page="checkout")], {}


def g_add_with_delivery():
    p, t = product_mention(), rng.choice(TIMES)
    checkout = rng.random() < 0.5
    msg = rng.choice(["add {p} and deliver it {t}", "I need {p} delivered {t}", "add {p}, delivery {t}",
                      "get {p} {t}"]).format(p=p, t=t)
    calls = [call("add_to_cart", items=[line(p)]), call("set_delivery_slot", when=t)]
    if checkout:
        msg += rng.choice([" and check out", ", then checkout", " and open checkout"])
        calls.append(call("navigate", page="checkout"))
    return msg, calls, {}


def g_delivery_only():
    t = rng.choice(TIMES)
    msg = rng.choice(["deliver my order {t}", "can it come {t}?", "schedule delivery {t}", "I want the delivery {t}",
                      "change delivery to {t}", "bring it {t}"]).format(t=t)
    return msg, [call("set_delivery_slot", when=t)], {"cart": True}


def g_search():
    kind = rng.random()
    if kind < 0.35:
        p = rng.choice(GENERIC + [b.lower() for b in BASES])
        msg = rng.choice(["best {p}", "which {p} is the best?", "top rated {p}", "show me good {p}"]).format(p=p)
        return msg, [call("search_products", query=p, sort="best")], {}
    if kind < 0.55:
        p = rng.choice(GENERIC)
        msg = rng.choice(["cheapest {p}", "lowest price {p}", "show the most affordable {p}"]).format(p=p)
        return msg, [call("search_products", query=p, sort="price_low")], {}
    if kind < 0.75:
        tag, phrase = rng.choice([("sugar-free", "sugar free"), ("organic", "organic"), ("gluten-free", "gluten free"),
                                  ("vegan", "vegan"), ("high-protein", "high protein"), ("low-fat", "low fat")])
        p = rng.choice(["snacks", "biscuits", "milk", "bread", "breakfast cereal", "drinks", "dal", "flour", "chocolate"])
        msg = rng.choice(["{ph} {p}", "show me {ph} {p}", "any {ph} {p}?", "looking for {ph} {p}"]).format(ph=phrase, p=p)
        return msg, [call("search_products", query=f"{phrase} {p}", tags=[tag])], {}
    if kind < 0.88:
        p, n = rng.choice(["chips", "biscuits", "juice", "chocolate", "noodles", "soap"]), rng.choice([20, 30, 50, 100, 150])
        msg = rng.choice(["{p} under {n} rupees", "{p} below ₹{n}", "{p} within {n} rs"]).format(p=p, n=n)
        return msg, [call("search_products", query=p, max_price=float(n))], {}
    q = rng.choice(["something for breakfast", "snacks for a movie night", "ingredients for pasta", "things for a party",
                    "stuff to clean the kitchen", "healthy evening snacks", "food for a toddler"])
    msg = rng.choice(["show me {q}", "suggest {q}", "{q}"]).format(q=q)
    return msg, [call("search_products", query=q.replace("something for ", "").replace("show me ", ""))], {}


def g_find_orders():
    d = rng.choice(DATES)
    if rng.random() < 0.3:
        p = rng.choice(GENERIC)
        msg = rng.choice(["when did I last buy {p}", "orders that had {p}", "show orders containing {p}",
                          "find my {p} orders"]).format(p=p)
        return msg, [call("find_orders", contains=p)], {}
    msg = rng.choice(["what did I get {d}", "pull up my order from {d}", "show me what I bought {d}",
                      "my order {d}", "open the order from {d}", "what was in my {d} order"]).format(d=d)
    return msg, [call("find_orders", when=d.removeprefix("on ").removeprefix("in "))], {}


def g_reorder():
    if rng.random() < 0.2:
        d = rng.choice(["last saturday", "last tuesday", "yesterday", "10th august", "march 5"])
        msg = rng.choice(["reorder the stuff I bought {d}", "reorder my groceries from {d}"]).format(d=d)
        return msg, [call("reorder", order=d)], {}
    if rng.random() < 0.5:
        msg = rng.choice(["repeat my last order", "order the same as last time", "buy my previous order again",
                          "same order as before please"])
        return msg, [call("reorder", order="last")], {}
    d = rng.choice([x for x in DATES if "month" not in x and "week" not in x and "days" not in x])
    msg = rng.choice(["buy again what I ordered {d}", "reorder my {d} order", "repeat the order from {d}"]).format(d=d)
    return msg, [call("reorder", order=d.removeprefix("on "))], {}


def g_navigate():
    page, phr = rng.choice([("checkout", "checkout page"), ("orders", "order history"), ("home", "home page"),
                            ("reminders", "reminders"), ("orders", "my past orders"), ("checkout", "payment page")])
    msg = rng.choice(["bring up the {x}", "jump to the {x}", "let me see the {x}", "navigate to {x}",
                      "I'd like to go to the {x}"]).format(x=phr)
    return msg, [call("navigate", page=page)], {}


def g_view_cart():
    msg = rng.choice(["let me see my basket", "show what's in the bag", "review my cart", "what have I added so far?",
                      "show my cart items"])
    return msg, [call("view_cart")], {"cart": True}


CART_LINES = [("Amul Toned Milk", "1 L"), ("Britannia Brown Bread", "400 g"), ("Lay's Classic Salted Potato Chips", "52 g"),
              ("Eggoz Brown Eggs", "Pack of 12"), ("Tata Tea Premium Black Tea", "500 g"), ("FarmFresh Onion", "1 kg"),
              ("Maggi Masala Instant Noodles", "Pack of 4"), ("Amul Fresh Paneer", "200 g")]


def g_update_cart():
    lines = rng.sample(CART_LINES, 3)
    target = rng.choice(lines)
    short = rng.choice([target[0].split()[-1].lower(), " ".join(target[0].split()[-2:]).lower()])
    if rng.random() < 0.5:
        msg = rng.choice(["remove the {s}", "take {s} out", "drop the {s} from my cart", "I don't want the {s}"]).format(s=short)
        q = 0
    else:
        q = rng.choice([1, 2, 3, 4])
        msg = rng.choice(["make the {s} {q}", "change {s} quantity to {q}", "set {s} to {q}", "only {q} {s} please"]
                         ).format(s=short, q=q)
    return msg, [call("update_cart_item", product=short, quantity=q)], {"cart_lines": lines}


def g_clear_cart():
    msg = rng.choice(["remove everything from my cart", "start over with an empty cart", "wipe my basket",
                      "delete all items in the cart"])
    return msg, [call("clear_cart")], {"cart": True}


def g_place_order():
    msg = rng.choice(["place the order now", "pay for this", "confirm and place my order", "go ahead and order it",
                      "submit my order", "complete the purchase"])
    return msg, [call("place_order")], {"cart": True}


def g_reminder():
    p = rng.choice(GENERIC)
    if rng.random() < 0.6:
        d, h = rng.choice(DAYS), rng.choice([7, 8, 9, 10, 18, 19, 20])
        hp = f"{h % 12 or 12} {'am' if h < 12 else 'pm'}"
        msg = rng.choice(["remind me every {d} at {hp} to buy {p}", "every {d} {hp} reminder for {p}",
                          "set a weekly reminder on {d}s at {hp} to get {p}"]).format(d=d, hp=hp, p=p)
        return msg, [call("set_reminder", description=f"buy {p}", frequency="weekly", day_of_week=d, hour=h, products=[p])], {}
    h = rng.choice([6, 7, 8, 9])
    msg = rng.choice(["remind me daily at {h} am to order {p}", "daily {h} am reminder for {p}"]).format(h=h, p=p)
    return msg, [call("set_reminder", description=f"order {p}", frequency="daily", hour=h, products=[p])], {}


def g_details():
    p = rng.choice(BRANDED).lower()
    msg = rng.choice(["tell me about {p}", "what do people think of {p}?", "is {p} worth buying?", "open {p}",
                      "reviews for {p}"]).format(p=p)
    return msg, [call("get_product_details", product=p)], {}


def g_on_screen():
    word, n = rng.choice(ORDINALS)
    n = n or 6
    shown = rng.sample(CATALOG, 6)
    ref = f"#{n}"
    if rng.random() < 0.6:
        q = rng.choice([1, 1, 2])
        msg = rng.choice(["add the {w} one", "add {qp} of the {w} one", "I'll take the {w}", "add #{n}"]).format(
            w=word, n=n, qp=qty_phrase(q) or "one")
        q = q if "of the" in msg else 1
        return msg, [call("add_to_cart", items=[line(ref, q)])], {"shown": shown}
    msg = rng.choice(["open the {w} one", "more about the {w}", "details of #{n}"]).format(w=word, n=n)
    return msg, [call("get_product_details", product=ref)], {"shown": shown}


def g_bare_noun():
    p = rng.choice(GENERIC + HINGLISH + [b.lower() for b in BASES])
    msg = rng.choice(["{p}", "got any {p}?", "looking for {p}", "do you sell {p}", "{p} options", "show {p}"]).format(p=p)
    return msg, [call("search_products", query=p)], {}


def _results_for(p: str, n: int = 4) -> list[dict]:
    pool = [c for c in CATALOG if p.split()[-1].rstrip("s") in c["name"].lower()] or rng.sample(CATALOG, n)
    picks = rng.sample(pool, min(n, len(pool)))
    return [{"ref": f"#{i}", "name": c["name"], "label": c["variants"][0]["label"], "price": float(c["variants"][0]["price"]),
             "rating": round(rng.uniform(3.4, 4.8), 1), "in_stock": True, "bought_before": rng.random() < 0.2}
            for i, c in enumerate(picks, 1)]


def g_search_then_add():
    """Two-step: search first; after the results come back, add #1."""
    p = rng.choice(GENERIC + [b.lower() for b in BASES])
    sort, phr = rng.choice([("best", "best"), ("best", "top rated"), ("price_low", "cheapest")])
    msg = rng.choice(["find the {ph} {p} and add it", "search for the {ph} {p} and put it in my cart",
                      "get the {ph} {p} into my cart", "pick the {ph} {p} for me and add it"]).format(ph=phr, p=p)
    first = [call("search_products", query=p, sort=sort)]
    if rng.random() < 0.5:
        return msg, first, {}
    results = _results_for(p)
    tail = [{"role": "assistant", "content": "", "tool_calls": first},
            {"role": "tool", "content": json.dumps({"results": results, "count": len(results)}, ensure_ascii=False)}]
    return msg, [call("add_to_cart", items=[line("#1")])], {"tail": tail}


def g_reply_after_add():
    """Final step: after a successful add, reply in one short sentence without listing the cart."""
    c = rng.choice(CATALOG)
    q = rng.choice([1, 1, 2])
    p = c["base"].lower()
    added = f"{q} x {c['name']} ({c['variants'][0]['label']}) @ ₹{c['variants'][0]['price']}"
    tail = [{"role": "assistant", "content": "", "tool_calls": [call("add_to_cart", items=[line(p, q)])]},
            {"role": "tool", "content": json.dumps({"added": [added], "cart": {"item_count": q + 1, "total": 240.0}},
                                                   ensure_ascii=False)}]
    reply = rng.choice(["Added {x}.", "Done, {x} is in your cart.", "Added {x} to your cart."]).format(
        x=f"{q} x {c['name']} ({c['variants'][0]['label']})")
    return f"add {qty_phrase(q)} {p}".replace("  ", " "), reply, {"tail": tail}


def g_no_tool():
    msg, reply = rng.choice([
        ("what's the weather today?", "I can only help with shopping in Converse, like products, your cart and orders."),
        ("delete my account", "I can't change account details, but I can help with products, your cart and orders."),
        ("cancel all my previous orders", "I can't cancel or delete past orders. I can show them or reorder one for you."),
        ("tell me a joke", "I'm your shopping assistant, so I'll stick to groceries. What can I get you?"),
        ("change my address", "I can't change account details here. Anything I can add to your cart?"),
        ("hi", "Hi! Tell me what you need and I'll find it, add it or take you there."),
        ("thanks!", "You're welcome!"),
    ])
    return msg, reply, {}


GENERATORS = [(g_add_single, 14), (g_add_multi, 8), (g_usual, 4), (g_add_then_checkout, 8), (g_add_with_delivery, 6),
              (g_delivery_only, 4), (g_search, 12), (g_find_orders, 9), (g_reorder, 4), (g_navigate, 5), (g_view_cart, 3),
              (g_update_cart, 7), (g_clear_cart, 2), (g_place_order, 3), (g_reminder, 4), (g_details, 4),
              (g_on_screen, 7), (g_no_tool, 3), (g_bare_noun, 5), (g_search_then_add, 5), (g_reply_after_add, 3)]


# ---------------- context rendering (same format as app/agent/loop.py) ----------------
def render_context(msg: str, ctx: dict) -> str:
    shown = ctx.get("shown") or []
    screen = "\n".join(f"#{i} {p['name']} ({p['variants'][0]['label']}) ₹{p['variants'][0]['price']}"
                       for i, p in enumerate(shown, 1)) or "(nothing listed)"
    lines = ctx.get("cart_lines") or (rng.sample(CART_LINES, rng.randint(1, 3)) if ctx.get("cart") or rng.random() < 0.4 else [])
    if lines:
        qts = [rng.randint(1, 3) for _ in lines]
        total = sum(rng.randint(40, 200) * q for q in qts)
        cart = "\n".join(f"- {n} ({lb}) x{q}" for (n, lb), q in zip(lines, qts, strict=True)) + f"\ntotal ₹{total}"
    else:
        cart = "empty"
    route = rng.choice(["/", "/cart", "/search", "/orders", f"/product/{rng.randint(1, 340)}"]) if not shown else "/search"
    return CONTEXT.format(route=route, screen=screen, cart=cart, message=msg)


def system_prompt() -> str:
    d = date(2026, 1, 1) + timedelta(days=rng.randint(0, 300))
    return SYSTEM.format(today=d.strftime("%A, %d %B %Y"), name=rng.choice(NAMES))


def history(n_turns: int) -> list[dict]:
    """Earlier turns in the agent's structured history format (tool calls + one-line results + reply)."""
    out = []
    for _ in range(n_turns):
        gen = rng.choices([g for g, _ in GENERATORS], weights=[w for _, w in GENERATORS])[0]
        msg, target, _ = gen()
        out.append({"role": "user", "content": msg})
        if isinstance(target, str):
            out.append({"role": "assistant", "content": target})
            continue
        out.append({"role": "assistant", "content": "", "tool_calls": target})
        for c in target:
            out.append({"role": "tool", "content": json.dumps({"result": f"{c['function']['name']} done"})})
        out.append({"role": "assistant", "content": "Done."})
    return out


def too_close_to_eval(msg: str) -> bool:
    m = msg.lower()
    return any(fuzz.ratio(m, e) >= 85 for e in EVAL_UTTERANCES)


def example() -> dict | None:
    gen = rng.choices([g for g, _ in GENERATORS], weights=[w for _, w in GENERATORS])[0]
    msg, target, ctx = gen()
    if too_close_to_eval(msg):
        return None
    hist = history(rng.choice([0, 0, 0, 1, 1, 2]))
    if hist and rng.random() < 0.25:
        # "repeat request" pattern: the same request already appears in history; the model must still act
        hist += [{"role": "user", "content": msg},
                 {"role": "assistant", "content": "", "tool_calls": target} if not isinstance(target, str)
                 else {"role": "assistant", "content": target}]
        if not isinstance(target, str):
            hist += [{"role": "tool", "content": json.dumps({"result": "done"})}, {"role": "assistant", "content": "Done."}]
    messages = [{"role": "system", "content": system_prompt()}, *hist,
                {"role": "user", "content": render_context(msg, ctx)}, *ctx.get("tail", [])]
    if isinstance(target, str):
        messages.append({"role": "assistant", "content": target})
    else:
        messages.append({"role": "assistant", "content": "", "tool_calls": target})
    return {"messages": messages, "tools": TOOLS, "_intent": gen.__name__}


def main(n_train: int = 2400, n_valid: int = 160) -> None:
    OUT.mkdir(exist_ok=True)
    rows, seen, dropped = [], {}, 0
    while len(rows) < n_train + n_valid:
        ex = example()
        if ex is None:
            dropped += 1
            continue
        user_turn = next(m for m in reversed(ex["messages"]) if m["role"] == "user")
        key = user_turn["content"].split("[user]\n")[-1] + json.dumps(ex["messages"][-1].get("tool_calls") or ex["messages"][-1]["content"])
        # the same sentence in a different context (cart, screen, history, date) is still a distinct example
        if seen.get(key, 0) >= 6:
            continue
        seen[key] = seen.get(key, 0) + 1
        rows.append(ex)
    rng.shuffle(rows)
    from collections import Counter

    print("intents:", dict(Counter(r["_intent"] for r in rows)))
    for name, part in (("train", rows[:n_train]), ("valid", rows[n_train:])):
        with open(OUT / f"{name}.jsonl", "w") as f:
            for r in part:
                f.write(json.dumps({k: v for k, v in r.items() if not k.startswith("_")}, ensure_ascii=False) + "\n")
    print(f"wrote {n_train} train / {n_valid} valid; dropped {dropped} too close to eval utterances")


if __name__ == "__main__":
    main()
