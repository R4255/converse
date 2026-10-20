"""Deterministic resolvers. Small LLMs are unreliable at date math and entity matching,
so the model passes the user's words through and these turn them into concrete values."""

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

import dateparser
from rapidfuzz import fuzz
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models import Order, OrderItem, Product, Variant
from app.search.engine import Filters, canonical_query, search_engine, tokenize

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september",
          "october", "november", "december"]
ORDINALS = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4,
            "fifth": 5, "5th": 5, "sixth": 6, "6th": 6, "seventh": 7, "eighth": 8}


# ---------------- dates ----------------
@dataclass
class DateRange:
    start: date
    end: date
    label: str


def resolve_when(expr: str | None, today: date) -> DateRange | None:
    """'5th january' / 'last friday' / 'last week' / 'in march' / 'last 10 days' -> inclusive range.
    Dates are always resolved into the past (orders can't be in the future)."""
    if not expr or not expr.strip():
        return None
    e = expr.lower().strip()
    e = re.sub(r"^(on|in|of|from|during|for)\s+", "", e)
    e = re.sub(r"\s+", " ", e)

    def one(d: date, label: str | None = None) -> DateRange:
        return DateRange(d, d, label or d.strftime("%a %d %b %Y"))

    if e in ("today", "now"):
        return one(today, "today")
    if e == "yesterday":
        return one(today - timedelta(days=1), "yesterday")
    if e in ("day before yesterday", "the day before yesterday"):
        return one(today - timedelta(days=2))
    if m := re.fullmatch(r"(?:the )?(?:last|past|previous) (\d+) days?", e):
        return DateRange(today - timedelta(days=int(m[1])), today, f"last {m[1]} days")
    if e in ("this week",):
        start = today - timedelta(days=today.weekday())
        return DateRange(start, today, "this week")
    if e in ("last week", "previous week"):
        start = today - timedelta(days=today.weekday() + 7)
        return DateRange(start, start + timedelta(days=6), "last week")
    if e == "this month":
        return DateRange(today.replace(day=1), today, "this month")
    if e in ("last month", "previous month"):
        end = today.replace(day=1) - timedelta(days=1)
        return DateRange(end.replace(day=1), end, end.strftime("%B %Y"))
    if m := re.fullmatch(r"(last |this |previous )?(" + "|".join(WEEKDAYS) + r")", e):
        wd = WEEKDAYS.index(m[2])
        back = (today.weekday() - wd) % 7
        if back == 0 or (m[1] or "").strip() in ("last", "previous"):
            back = back or 7
        return one(today - timedelta(days=back))
    if m := re.fullmatch(r"(?:the month of )?(" + "|".join(MONTHS) + r")(?: (\d{4}))?", e):
        mi = MONTHS.index(m[1]) + 1
        year = int(m[2]) if m[2] else (today.year if mi <= today.month else today.year - 1)
        start = date(year, mi, 1)
        end = (date(year + (mi == 12), mi % 12 + 1, 1) - timedelta(days=1))
        return DateRange(start, min(end, today), start.strftime("%B %Y"))

    parsed = dateparser.parse(
        e, settings={"PREFER_DATES_FROM": "past", "DATE_ORDER": "DMY",
                     "RELATIVE_BASE": datetime.combine(today, time(12))})
    if parsed:
        d = parsed.date()
        if d > today:  # "5th january" said in october means the past January
            d = d.replace(year=d.year - 1)
        return one(d)
    return None


# ---------------- product references ----------------
@dataclass
class Resolved:
    status: str  # "ok" | "ambiguous" | "not_found"
    product: Product | None = None
    variant: Variant | None = None
    options: list[dict] = field(default_factory=list)
    reason: str = ""


def _ordinal(ref: str, shown: list[dict]) -> dict | None:
    r = ref.lower().strip()
    r = re.sub(r"\b(the|one|item|product|option|number|no\.?)\b", " ", r).strip()
    if m := re.fullmatch(r"#?\s*(\d{1,2})", r):
        n = int(m[1])
    elif r in ORDINALS:
        n = ORDINALS[r]
    elif r in ("last", "last one"):
        n = len(shown)
    else:
        return None
    return shown[n - 1] if 1 <= n <= len(shown) else None


_SIZE = re.compile(r"(\d+(?:\.\d+)?)\s*(ml|l|ltr|litre|liter|g|gm|kg|pcs|pc|pack)\b", re.I)


def _norm_label(s: str) -> str:
    s = s.lower().replace("ltr", "l").replace("litre", "l").replace("liter", "l").replace("gm", "g")
    return re.sub(r"\s+", "", s)


def pick_variant(p: Product, size: str | None, habitual_variant_id: int | None = None) -> Variant | None:
    vs = [v for v in p.variants if v.stock > 0] or list(p.variants)
    if size:
        s = size.lower().strip()
        words = set(re.findall(r"[a-z]+", s))
        if words & {"large", "big", "bigger", "biggest", "family", "jumbo"}:
            return max(vs, key=lambda v: v.price)
        if words & {"small", "smaller", "smallest", "mini"}:
            return min(vs, key=lambda v: v.price)
        ns = _norm_label(s)
        for v in vs:
            if _norm_label(v.label) == ns or ns in _norm_label(v.label):
                return v
        best = max(vs, key=lambda v: fuzz.ratio(_norm_label(v.label), ns))
        if fuzz.ratio(_norm_label(best.label), ns) >= 70:
            return best
        return None
    if habitual_variant_id:
        for v in vs:
            if v.id == habitual_variant_id:
                return v
    return vs[0]


async def purchase_counts(db: AsyncSession, user_id: int) -> dict[int, tuple[int, int]]:
    """product_id -> (times bought, most-bought variant id). Powers 'my usual milk'."""
    rows = await db.execute(
        select(Variant.product_id, OrderItem.variant_id, func.count())
        .join(Order, Order.id == OrderItem.order_id).join(Variant, Variant.id == OrderItem.variant_id)
        .where(Order.user_id == user_id).group_by(Variant.product_id, OrderItem.variant_id))
    per_variant: dict[int, dict[int, int]] = {}
    for pid, vid, n in rows:
        per_variant.setdefault(pid, {})[vid] = n
    out = {pid: (sum(vs.values()), max(vs, key=vs.get)) for pid, vs in per_variant.items()}
    return out


async def resolve_product(db: AsyncSession, ref: str, size: str | None, shown: list[dict],
                          history: dict[int, tuple[int, int]]) -> Resolved:
    ref = (ref or "").strip()
    if not ref:
        return Resolved("not_found", reason="empty product reference")

    # 1) on-screen reference: "#2", "the second one", "last one" (also "#2 Amul Cheese")
    lead = re.match(r"#\s*(\d{1,2})\b", ref)
    if lead and not _ordinal(lead[0], shown) and len(ref) <= 4:
        return Resolved("not_found", reason=f"there is no item {ref} on screen")
    if hit := (_ordinal(lead[0], shown) if lead else _ordinal(ref, shown)):
        p = await db.get(Product, hit["product_id"])
        v = pick_variant(p, size or hit.get("label"), hit.get("variant_id"))
        return Resolved("ok", p, v) if v else _size_problem(p, size)

    # 2) a name that matches something on screen ("the mozzarella from that order")
    if shown:
        scored = [(fuzz.token_set_ratio(ref.lower(), s["name"].lower()), s) for s in shown]
        best_score, best = max(scored, key=lambda x: x[0])
        if best_score >= 85 and sum(1 for sc, _ in scored if sc == best_score) == 1:
            p = await db.get(Product, best["product_id"])
            v = pick_variant(p, size or best.get("label"), best.get("variant_id"))
            if v:
                return Resolved("ok", p, v)

    # 3) explicit id passed back from an earlier tool result
    if m := re.fullmatch(r"(?:id[:\s]*|product[_ ]id[:\s]*)?(\d{1,6})", ref.lower()):
        if p := await db.get(Product, int(m[1])):
            v = pick_variant(p, size, history.get(p.id, (0, None))[1])
            return Resolved("ok", p, v) if v else _size_problem(p, size)

    # 4) name lookup through the search engine, then disambiguate
    ref, size = _extract_size(ref, size)
    usual = bool(re.search(r"\b(usual|regular|always|normal)\b", ref, re.I))
    q = re.sub(r"\b(my|usual|regular|the|always|normal|one|that|i|buy|get|some|a|an)\b", " ", ref, flags=re.I)
    q = _apply_aliases(re.sub(r"\s+", " ", q).strip() or ref)
    res = await search_engine.search(db, q, Filters(in_stock_only=False), k=8, mode="full")
    cands = [search_engine.by_id[h.product_id] for h in res.hits]
    rel = {h.product_id: h.stages.get("relevance", 0) for h in res.hits}
    if not cands:
        return Resolved("not_found", reason=f"no product matches '{ref}'")

    strong = [c for c in cands if rel.get(c.id, 0) >= 0.5] or cands[:1]
    # Cross-encoders are confident about one item for one-word queries ("oil"); every candidate whose
    # name/subcategory contains all the query words is an equally valid reading, so pool them.
    qtoks = {t.rstrip("s") for t in tokenize(canonical_query(q))}
    lexical = [c for c in cands if qtoks and qtoks <= {t.rstrip("s") for t in tokenize(f"{c.name} {c.subcategory}")}]
    if len(lexical) > len(strong):
        strong = lexical
    if size:  # a specific size narrows to products that actually come in that size
        sized = [c for c in strong if pick_variant(await db.get(Product, c.id), size) is not None]
        strong = sized or strong
    brand = _brand_in(q)
    if brand:
        strong = [c for c in strong if brand in _norm(c.brand)] or strong
    exact = [c for c in strong if fuzz.token_set_ratio(q.lower(), c.name.lower()) >= 95 and len(q.split()) >= 2]
    # Habits: "my usual X" uses any purchase; otherwise only products bought repeatedly count as a preference.
    min_buys = 1 if usual else HABIT_MIN_PURCHASES
    bought = sorted([c for c in strong if history.get(c.id, (0, None))[0] >= min_buys], key=lambda c: -history[c.id][0])

    if len(exact) == 1:
        chosen = exact[0]
    elif bought:
        chosen = bought[0]
    elif len(strong) == 1:
        chosen = strong[0]
    elif brand or size:
        # The user was specific (brand or size) -> take the most popular remaining match.
        chosen = max(strong, key=lambda c: c.popularity)
    else:
        # Generic request ("add cheese"), no history, several real candidates -> ask.
        opts = []
        for c in strong[:4]:
            p = await db.get(Product, c.id)
            opts.append({"product_id": p.id, "name": p.name, "price_from": float(p.variants[0].price),
                         "sizes": [v.label for v in p.variants]})
        return Resolved("ambiguous", options=opts, reason=f"several products match '{ref}'; ask which one")

    p = await db.get(Product, chosen.id)
    v = pick_variant(p, size, history.get(p.id, (0, None))[1])
    return Resolved("ok", p, v) if v else _size_problem(p, size)


HABIT_MIN_PURCHASES = 8  # real habits are bought ~50x/yr; one-off exploration purchases stay below 5
_FILLER = re.compile(r"\b(pack|packet|packets|packs|bottle|bottles|box|bag|tin|can|cans|jar)s?\s+of\b", re.I)
_SIZE_WORD = re.compile(
    r"\b(big|large|bigger|biggest|family|jumbo|small|smaller|smallest|mini)\b(\s+(pack|size|bottle|one))?", re.I)
_COUNT = re.compile(r"\b(\d+)\s*(eggs?|pcs|pieces)\b", re.I)
ALIASES = {"coke": "coca-cola", "lays": "lay's", "haldirams": "haldiram's", "kelloggs": "kellogg's",
           "dairy milk": "cadbury dairy milk", "parle g": "parle-g"}


def _extract_size(ref: str, size: str | None) -> tuple[str, str | None]:
    """Pull size phrases out of a product reference: '5 kg pack of atta', 'big pack of oil', '30 eggs'."""
    ref = _FILLER.sub(" ", ref)
    if m := _SIZE.search(ref):
        size = size or m[0]
        ref = ref[: m.start()] + ref[m.end():]
    if m := _COUNT.search(ref):
        size = size or m[1]
        ref = ref[: m.start()] + m[2] + ref[m.end():]
    if m := _SIZE_WORD.search(ref):
        size = size or m[1]
        ref = ref[: m.start()] + ref[m.end():]
    return re.sub(r"\s+", " ", ref).strip(), size


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", s.lower().replace("-", " "))


def _apply_aliases(q: str) -> str:
    low = q.lower()
    for k, v in ALIASES.items():
        low = re.sub(rf"\b{re.escape(k)}\b", v, low)
    return low


def _brand_in(q: str) -> str | None:
    """Normalised brand (or brand's distinctive first word) mentioned in the query."""
    nq = f" {_norm(q)} "
    brands = sorted({_norm(d.brand) for d in search_engine.docs}, key=len, reverse=True)
    if full := next((b for b in brands if f" {b} " in nq), None):
        return full
    firsts = {b.split()[0] for b in brands if len(b.split()[0]) > 3}
    return next((w for w in nq.split() if w in firsts), None)


def _size_problem(p: Product, size: str | None) -> Resolved:
    return Resolved("ambiguous", product=p, reason=f"size '{size}' not available for {p.name}",
                    options=[{"product_id": p.id, "name": p.name, "sizes": [v.label for v in p.variants]}])


# ---------------- delivery slots ----------------
SLOT_FIRST_HOUR, SLOT_LAST_HOUR = 6, 22  # 1-hour slots from 6-7 AM to 10-11 PM
SLOT_MIN_LEAD = timedelta(minutes=30)
SLOT_MAX_DAYS = 3
_PARTS_OF_DAY = {"early morning": 6, "morning": 8, "noon": 12, "afternoon": 14, "evening": 18, "night": 20, "tonight": 20}


@dataclass
class SlotResolution:
    start: datetime | None  # None = express delivery (~10 minutes)
    label: str
    note: str = ""


def slot_label(start: datetime | None) -> str:
    if start is None:
        return "Express (~10 min)"
    end = start + timedelta(hours=1)
    return f"{start.strftime('%a %d %b')}, {start.strftime('%-I %p')}–{end.strftime('%-I %p')}"


def earliest_slot(now: datetime) -> datetime:
    t = (now + SLOT_MIN_LEAD).replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    if t.hour < SLOT_FIRST_HOUR:
        t = t.replace(hour=SLOT_FIRST_HOUR)
    elif t.hour > SLOT_LAST_HOUR:
        t = (t + timedelta(days=1)).replace(hour=SLOT_FIRST_HOUR)
    return t


def resolve_delivery_time(expr: str, now: datetime) -> SlotResolution | None:
    """'tomorrow around 5 am' / 'this evening' / 'asap' -> a concrete slot, snapped to delivery hours."""
    e = re.sub(r"\b(around|about|approx(imately)?|at|by|on|deliver(y|ed)?|please|ish|maybe)\b|~", " ", expr.lower())
    e = re.sub(r"\s+", " ", e).strip()
    if not e or re.search(r"\b(asap|now|express|immediately|right away|quick(ly)?)\b", e):
        return SlotResolution(None, slot_label(None))

    day = now.date()
    if "day after tomorrow" in e:
        day, e = day + timedelta(days=2), e.replace("day after tomorrow", "")
    elif "tomorrow" in e:
        day, e = day + timedelta(days=1), e.replace("tomorrow", "")
    hour = minute = None
    if m := re.search(r"\bin (\d+|an?|one|two|three) (hours?|days?|weeks?)\b", e):
        n = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3}.get(m[1]) or int(m[1])
        unit = m[2].rstrip("s")
        target = now + (timedelta(hours=n) if unit == "hour" else timedelta(days=n * (7 if unit == "week" else 1)))
        day, hour, minute = target.date(), target.hour, target.minute
        if unit != "hour":
            hour, minute = (now + SLOT_MIN_LEAD).hour + 1, 0
    elif m := re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?", e):
        hour, minute = int(m[1]), int(m[2] or 0)
        mer = (m[3] or "").replace(".", "")
        if mer == "pm" and hour < 12:
            hour += 12
        elif mer == "am" and hour == 12:
            hour = 0
        elif not mer and hour < 7:  # "at 6" without am/pm means evening in a grocery context
            hour += 12
    else:
        for part, h in _PARTS_OF_DAY.items():
            if part in e:
                hour, minute = h, 0
                break
    if hour is None:
        parsed = dateparser.parse(e, settings={"PREFER_DATES_FROM": "future", "RELATIVE_BASE": now.replace(tzinfo=None)})
        if not parsed:
            return None
        day, hour, minute = parsed.date(), parsed.hour, parsed.minute
    if hour > 23:
        return None

    if re.search(r"\b(tonight|night)\b", expr.lower()) and hour < SLOT_FIRST_HOUR and day == now.date():
        day += timedelta(days=1)  # "3 am tonight" means the small hours after midnight
    wanted = datetime.combine(day, time(hour, minute or 0), now.tzinfo)
    start = wanted.replace(minute=0)
    note = ""
    if start.hour < SLOT_FIRST_HOUR:
        start = start.replace(hour=SLOT_FIRST_HOUR)
        note = f"We deliver from {SLOT_FIRST_HOUR} AM, so the earliest slot that day is {slot_label(start).split(', ')[1]}."
    elif start.hour > SLOT_LAST_HOUR:
        start = start.replace(hour=SLOT_LAST_HOUR)
        note = f"The last delivery slot is {slot_label(start).split(', ')[1]}."
    earliest = earliest_slot(now)
    if start < earliest:
        start = earliest
        note = f"That time is too soon or has passed; the earliest slot is {slot_label(start)}."
    if start.date() > now.date() + timedelta(days=SLOT_MAX_DAYS):
        return SlotResolution(None, slot_label(None), f"Slots can be booked up to {SLOT_MAX_DAYS} days ahead.")
    return SlotResolution(start, slot_label(start), note)
