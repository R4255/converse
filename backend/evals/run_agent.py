"""Agent eval runner.

    uv run python -m evals.run_agent --tag baseline
    uv run python -m evals.run_agent --tag coder --model qwen2.5-coder:7b
    uv run python -m evals.run_agent --tag no-router --no-router --category navigation
"""

import argparse
import asyncio
import json
import os
import re
import statistics
import time
import uuid
from collections import defaultdict
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

os.environ.setdefault("FROZEN_TODAY", "2026-10-19")

from sqlalchemy import delete, select  # noqa: E402

from app.agent.loop import _CLAIMS_ACTION, Agent, collect  # noqa: E402
from app.agent.protocol import TurnRequest  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.domain import services as svc  # noqa: E402
from app.domain.models import CartItem, DeliveryPreference, PendingAction, Product, Reminder  # noqa: E402
from app.search.engine import search_engine  # noqa: E402
from evals.tasks import TASKS  # noqa: E402

RESULTS = Path(__file__).parent / "results"
MUTATING = {"add_to_cart", "update_cart_item", "reorder", "set_reminder"}


async def fixtures() -> dict:
    async with SessionLocal() as db:
        jan5 = await svc.list_orders(db, 1, date(2026, 1, 5), date(2026, 1, 5))
        fri = await svc.list_orders(db, 1, date(2026, 10, 16), date(2026, 10, 16))
    return {"jan5_order": jan5[0].id, "fri_order": fri[0].id}


async def setup(user_id: int, lines: list[dict]) -> None:
    async with SessionLocal() as db:
        await db.execute(delete(CartItem).where(CartItem.user_id == user_id))
        await db.execute(delete(Reminder).where(Reminder.user_id == user_id))
        await db.execute(delete(PendingAction).where(PendingAction.user_id == user_id))
        await db.execute(delete(DeliveryPreference).where(DeliveryPreference.user_id == user_id))
        for ln in lines:
            p = (await db.execute(select(Product).where(Product.name == ln["name"]))).scalar_one()
            v = next(v for v in p.variants if v.label == ln["label"])
            db.add(CartItem(user_id=user_id, variant_id=v.id, quantity=ln["qty"]))
        await db.commit()


async def cart_state(user_id: int) -> list[dict]:
    async with SessionLocal() as db:
        cart = await svc.get_cart(db, user_id)
    for ln in cart["items"]:
        ln["subcategory"] = search_engine.by_id[ln["product_id"]].subcategory
    return cart["items"]


def _line_ok(line: dict, want: dict) -> bool:
    if "name" in want and want["name"].lower() not in line["name"].lower():
        return False
    if "subcategory" in want and want["subcategory"] != line["subcategory"]:
        return False
    if "label" in want and want["label"].lower() != line["label"].lower():
        return False
    return "qty" not in want or want["qty"] == line["quantity"]


def _products_ok(items: list[dict], pred: dict) -> str | None:
    if not items:
        return "no products shown"
    docs = [search_engine.by_id[i["product_id"]] for i in items]
    if (n := pred.get("min_count")) and len(items) < n:
        return f"only {len(items)} products shown"
    if (s := pred.get("any_name")) and not any(s.lower() in d.name.lower() for d in docs):
        return f"no shown product name contains '{s}'"
    if (t := pred.get("all_tag")) and not all(t in d.tags for d in docs):
        return f"not all products tagged {t}: {[d.name for d in docs if t not in d.tags][:3]}"
    if (mp := pred.get("all_max_price")) is not None and not all(i["price"] <= mp for i in items):
        return f"products above ₹{mp}"
    if (sc := pred.get("first_subcategory")) and docs[0].subcategory != sc:
        return f"first result is {docs[0].name} ({docs[0].subcategory}), expected {sc}"
    if (sc := pred.get("any_subcategory")) and not any(d.subcategory == sc for d in docs):
        return f"no product in {sc}"
    return None


def check(expect: dict, out: dict, before: list[dict], after: list[dict], prev_shown: list[dict],
          fx: dict, reminders: list[Reminder], slot=None) -> list[str]:
    fails = []
    tool_steps = [s for s in out["steps"] if s["kind"] == "tool"]
    called = [s["tool"] for s in tool_steps]
    ui = out["ui_actions"]
    navs = [a["payload"]["path"] for a in ui if a["type"] == "navigate"]
    confirms = [a["payload"]["action"] for a in ui if a["type"] == "confirm"]
    shown = [a for a in ui if a["type"] == "show_products"]
    snap = lambda c: {ln["variant_id"]: ln["quantity"] for ln in c}  # noqa: E731

    for t in expect.get("tools", []):
        if t not in called:
            fails.append(f"missing tool {t} (called {called})")
    for t in expect.get("no_tools", []):
        if t in called:
            fails.append(f"forbidden tool {t}")
    if (nav := expect.get("nav")) is not None:
        nav = nav.format(**fx)
        if not navs or navs[-1] != nav:
            fails.append(f"expected page {nav}, got {navs[-1] if navs else None}")
    if pred := expect.get("products"):
        if err := _products_ok(shown[-1]["payload"]["items"] if shown else [], pred):
            fails.append(err)
    if pred := expect.get("orders"):
        ranges = [str((s.get("result") or {}).get("date_range", "")) for s in tool_steps if s["tool"] == "find_orders"]
        if not any(pred["title_contains"].lower() in r.lower() for r in ranges):
            fails.append(f"orders range {ranges} lacks '{pred['title_contains']}'")
    if (c := expect.get("confirm")) and c not in confirms:
        fails.append(f"expected confirmation '{c}', got {confirms}")
    if expect.get("no_confirm") and confirms:
        fails.append(f"unexpected confirmation {confirms}")
    if expect.get("clarify"):
        asked = any(a["type"] == "clarify" for a in ui) or "?" in out.get("reply", "")
        if not asked:
            fails.append("did not ask a clarifying question")
        if snap(before) != snap(after):
            fails.append("mutated the cart instead of clarifying")
    for want in expect.get("cart", []):
        if not any(_line_ok(ln, want) for ln in after):
            fails.append(f"cart lacks {want}; cart={[(ln['name'], ln['label'], ln['quantity']) for ln in after]}")
    for name in expect.get("cart_not", []):
        if any(name.lower() in ln["name"].lower() for ln in after):
            fails.append(f"cart still has {name}")
    if expect.get("cart_unchanged") and snap(before) != snap(after):
        fails.append("cart changed")
    if (n := expect.get("cart_has_shown")) is not None:
        if len(prev_shown) < n:
            fails.append(f"previous turn showed {len(prev_shown)} products")
        elif prev_shown[n - 1]["product_id"] not in {ln["product_id"] for ln in after}:
            fails.append(f"cart lacks shown #{n} ({prev_shown[n - 1]['name']})")
    if expect.get("reply_has_cart_total"):
        total = sum(ln["line_total"] for ln in after)
        amounts = {float(x) for x in re.findall(r"\d+(?:\.\d+)?", (out.get("reply") or "").replace(",", ""))}
        if not any(abs(a - t) < 0.5 for a in amounts for t in (total, total + 5, total + 30)):
            fails.append(f"reply doesn't state the cart total (~₹{total}): {out.get('reply')}")
    if "slot_hour" in expect:
        want, got = expect["slot_hour"], (slot.astimezone(ZoneInfo("Asia/Kolkata")).hour if slot else None)
        if want != got:
            fails.append(f"delivery slot hour {got}, expected {want}")
    if (subs := expect.get("reply_mentions")) and not any(x.lower() in (out.get("reply") or "").lower() for x in subs):
        fails.append(f"reply lacks {subs}: {out.get('reply')}")
    if (cron := expect.get("reminder_cron")) and not any(cron in r.schedule_expression for r in reminders):
        fails.append(f"no reminder with cron containing '{cron}' ({[r.schedule_expression for r in reminders]})")
    return fails


async def run(args) -> dict:
    async with SessionLocal() as db:
        await search_engine.build(db)
    await search_engine.warmup()
    agent = Agent(model=args.model, router_enabled=not args.no_router, early_exit=not args.no_early_exit)
    await agent.llm.warmup()
    fx = await fixtures()
    tasks = [t for t in TASKS if (not args.category or t["category"] in args.category)
             and (not args.only or t["id"] in args.only)][: args.limit or None]

    records = []
    t_start = time.perf_counter()
    for i, t in enumerate(tasks, 1):
        await setup(t["user"], t["setup_cart"])
        session = f"eval-{uuid.uuid4().hex[:10]}"
        prev_shown: list[dict] = []
        turns, failures = [], []
        for j, turn in enumerate(t["turns"]):
            before = await cart_state(t["user"])
            async with SessionLocal() as db:
                out = await collect(agent, db, TurnRequest(user_id=t["user"], session_id=session,
                                                           turn_id=uuid.uuid4().hex, message=turn["say"]))
            after = await cart_state(t["user"])
            async with SessionLocal() as db:
                rems = list((await db.execute(select(Reminder).where(Reminder.user_id == t["user"]))).scalars())
                slot = await svc.get_delivery_slot(db, t["user"])
            fails = check(turn["expect"], out, before, after, prev_shown, fx, rems, slot)
            tool_steps = [s for s in out["steps"] if s["kind"] == "tool"]
            mutated = any(s["tool"] in MUTATING and not s.get("error") for s in tool_steps)
            confirmed = any(a["type"] == "confirm" for a in out["ui_actions"])
            hallucinated = bool(_CLAIMS_ACTION.search(out.get("reply", ""))) and not mutated and not confirmed \
                and not any(s["tool"] in ("place_order", "clear_cart") for s in tool_steps)
            turns.append({
                "say": turn["say"], "reply": out.get("reply"), "route": out.get("route"),
                "latency_ms": out.get("latency_ms"), "prompt_tokens": out.get("prompt_tokens", 0),
                "completion_tokens": out.get("completion_tokens", 0),
                "llm_calls": sum(1 for s in out["steps"] if s["kind"] == "llm"),
                "tools": [(s["tool"], s.get("args"), s.get("error")) for s in tool_steps],
                "invalid_args": sum(1 for s in tool_steps if (s.get("error") or "").startswith("invalid arguments")),
                "hallucinated_action": hallucinated, "error": out.get("error"), "fails": fails,
            })
            failures += [f"turn {j + 1}: {f}" for f in fails]
            shown = [a for a in out["ui_actions"] if a["type"] == "show_products"]
            if shown:
                prev_shown = shown[-1]["payload"]["items"]
        ok = not failures
        records.append({"id": t["id"], "category": t["category"], "passed": ok, "failures": failures, "turns": turns})
        mark = "PASS" if ok else "FAIL"
        print(f"[{i:3}/{len(tasks)}] {mark} {t['id']:<12} {turns[-1]['latency_ms'] / 1000:5.1f}s"
              + ("" if ok else f"  {failures[0][:150]}"), flush=True)
        await setup(t["user"], [])

    summary = summarize(records)
    summary["config"] = {"tag": args.tag, "model": agent.llm.model, "router": agent.router_enabled,
                         "early_exit": agent.early_exit, "tasks": len(records),
                         "wall_time_s": round(time.perf_counter() - t_start, 1)}
    RESULTS.mkdir(exist_ok=True)
    path = RESULTS / f"agent-{args.tag}.json"
    path.write_text(json.dumps({"summary": summary, "records": records}, indent=1, default=str))
    print(json.dumps(summary, indent=1))
    print(f"wrote {path}")
    return summary


def pct(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    k = (len(xs) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return round(xs[lo] + (xs[hi] - xs[lo]) * (k - lo), 1)


def summarize(records: list[dict]) -> dict:
    turns = [tn for r in records for tn in r["turns"]]
    by_cat: dict[str, list[bool]] = defaultdict(list)
    for r in records:
        by_cat[r["category"]].append(r["passed"])
    lat = [tn["latency_ms"] for tn in turns if tn["latency_ms"]]
    agent_lat = [tn["latency_ms"] for tn in turns if tn["route"] == "agent"]
    n_calls = sum(len(tn["tools"]) for tn in turns) or 1
    safety = [r for r in records if r["category"] == "safety"]
    return {
        "task_success": round(sum(r["passed"] for r in records) / len(records), 3),
        "by_category": {c: round(sum(v) / len(v), 3) for c, v in sorted(by_cat.items())},
        "turns": len(turns),
        "latency_ms": {"p50": pct(lat, 0.5), "p95": pct(lat, 0.95), "mean": round(statistics.mean(lat), 1) if lat else 0},
        "agent_route_latency_ms": {"p50": pct(agent_lat, 0.5), "p95": pct(agent_lat, 0.95)},
        "fast_route_share": round(sum(tn["route"] == "fast" for tn in turns) / len(turns), 3),
        "llm_calls_per_turn": round(sum(tn["llm_calls"] for tn in turns) / len(turns), 2),
        "tokens_per_turn": {"prompt": round(sum(tn["prompt_tokens"] for tn in turns) / len(turns)),
                            "completion": round(sum(tn["completion_tokens"] for tn in turns) / len(turns))},
        "invalid_tool_args_rate": round(sum(tn["invalid_args"] for tn in turns) / n_calls, 3),
        "hallucinated_action_rate": round(sum(tn["hallucinated_action"] for tn in turns) / len(turns), 3),
        "safety_pass_rate": round(sum(r["passed"] for r in safety) / len(safety), 3) if safety else None,
        "errors": sum(1 for tn in turns if tn["error"]),
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="dev")
    ap.add_argument("--model")
    ap.add_argument("--no-router", action="store_true")
    ap.add_argument("--no-early-exit", action="store_true")
    ap.add_argument("--category", nargs="*")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--limit", type=int)
    asyncio.run(run(ap.parse_args()))
