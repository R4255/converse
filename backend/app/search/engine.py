"""Hybrid product search: BM25 + pgvector dense -> RRF -> cross-encoder rerank -> business signals.

Every stage is timed so the eval harness and traces can attribute latency.
`mode` lets evals ablate stages: bm25 | dense | hybrid | rerank | full.
"""

import asyncio
import math
import re
import time
from dataclasses import dataclass, field

import bm25s
from rapidfuzz import fuzz, process
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.domain.models import Product
from app.search.embed import embed, product_document

# Hinglish / colloquial grocery vocabulary -> catalog vocabulary.
SYNONYMS = {
    "dahi": "curd", "doodh": "milk", "anda": "eggs", "ande": "eggs", "egg": "eggs",
    "chawal": "rice", "soya": "soy", "aloo": "potato", "pyaz": "onion", "pyaaz": "onion", "tamatar": "tomato",
    "palak": "spinach", "dhaniya": "coriander", "haldi": "turmeric", "namak": "salt",
    "cheeni": "sugar", "chini": "sugar", "chai": "tea", "makhana": "makhana fox nuts",
    "soda": "soft drink", "coke": "cola", "chips": "chips crisps", "diet": "sugar free zero sugar",
    "keto": "sugar-free high-protein low carb", "healthy": "whole-grain high-fiber low-fat",
    "gym": "high-protein protein", "diabetic": "sugar-free", "vegan": "vegan plant dairy free",
}
# How many logits a perfect rating/popularity signal is worth (cross-encoder logits span roughly -11..+11).
SIGNAL_WEIGHT = 1.0
SIGNAL_WEIGHT_BEST = 2.0
_TOKEN = re.compile(r"[a-z0-9]+")


def tokenize(s: str) -> list[str]:
    return _TOKEN.findall(s.lower())


def canonical_query(q: str) -> str:
    """Replace colloquial words with catalog vocabulary (for the cross-encoder, which has no synonym knowledge)."""
    return " ".join(SYNONYMS.get(t, t) for t in tokenize(q)) or q


def expand_query(q: str) -> str:
    extra = [SYNONYMS[t] for t in tokenize(q) if t in SYNONYMS]
    return " ".join([q, *extra])


@dataclass
class Filters:
    category: str | None = None
    brand: str | None = None
    tags: list[str] = field(default_factory=list)
    max_price: float | None = None
    in_stock_only: bool = True

    def match(self, p: "Doc") -> bool:
        if self.category and self.category.lower() not in (p.category.lower(), p.subcategory.lower()):
            return False
        if self.brand and self.brand.lower() not in p.brand.lower():
            return False
        if self.tags and not set(t.lower() for t in self.tags) <= set(p.tags):
            return False
        if self.max_price is not None and p.min_price > self.max_price:
            return False
        if self.in_stock_only and not p.in_stock:
            return False
        return True


@dataclass
class Doc:
    id: int
    name: str
    brand: str
    category: str
    subcategory: str
    tags: list[str]
    rating: float
    rating_count: int
    popularity: float
    min_price: float
    in_stock: bool
    text: str


@dataclass
class Hit:
    product_id: int
    score: float
    stages: dict[str, float]


@dataclass
class SearchResult:
    hits: list[Hit]
    timings_ms: dict[str, float]
    mode: str


class ProductSearch:
    def __init__(self) -> None:
        self.docs: list[Doc] = []
        self.vocab: list[str] = []
        self.by_id: dict[int, Doc] = {}
        self.bm25: bm25s.BM25 | None = None
        self._reranker = None
        self._qcache: dict[str, list[float]] = {}
        self._max_pop = 1.0

    async def build(self, db: AsyncSession) -> None:
        rows = (await db.execute(select(Product))).scalars().all()
        self.docs = [
            Doc(id=p.id, name=p.name, brand=p.brand, category=p.category, subcategory=p.subcategory,
                tags=[t.lower() for t in p.tags], rating=p.rating, rating_count=p.rating_count,
                popularity=p.popularity, min_price=float(min(v.price for v in p.variants)),
                in_stock=any(v.stock > 0 for v in p.variants), text=product_document(p))
            for p in rows
        ]
        self.by_id = {d.id: d for d in self.docs}
        self._max_pop = max((d.popularity for d in self.docs), default=1.0) or 1.0
        self.vocab = sorted({t for d in self.docs for t in tokenize(d.text)} | set(SYNONYMS))
        self.bm25 = bm25s.BM25(k1=1.2, b=0.75)
        self.bm25.index([tokenize(d.text) for d in self.docs], show_progress=False)

    def spell_correct(self, q: str) -> str:
        """Map out-of-vocabulary words to the closest catalog word ('tomoato' -> 'tomato')."""
        vocab = set(self.vocab)
        out = []
        for t in tokenize(q):
            if t in vocab or len(t) < 4 or t.isdigit():
                out.append(t)
                continue
            best = process.extractOne(t, self.vocab, scorer=fuzz.ratio, score_cutoff=80)
            out.append(best[0] if best else t)
        return " ".join(out) or q

    # ---------------- stages ----------------
    def _bm25(self, q: str, n: int) -> list[tuple[int, float]]:
        toks = tokenize(expand_query(q))
        if not toks:
            return []
        idx, scores = self.bm25.retrieve([toks], k=min(n, len(self.docs)), show_progress=False)
        return [(self.docs[i].id, float(s)) for i, s in zip(idx[0], scores[0], strict=True) if s > 0]

    async def _dense(self, db: AsyncSession, q: str, n: int) -> list[tuple[int, float]]:
        if q not in self._qcache:
            self._qcache[q] = (await embed([expand_query(q)], kind="query"))[0]
        vec = self._qcache[q]
        rows = await db.execute(
            text("SELECT id, 1 - (embedding <=> CAST(:v AS vector)) AS sim FROM products "
                 "ORDER BY embedding <=> CAST(:v AS vector) LIMIT :n"),
            {"v": str(vec), "n": n},
        )
        return [(r.id, float(r.sim)) for r in rows]

    @staticmethod
    def _rrf(*rankings: list[tuple[int, float]], k: int = 60) -> list[tuple[int, float]]:
        scores: dict[int, float] = {}
        for ranking in rankings:
            for rank, (pid, _) in enumerate(ranking):
                scores[pid] = scores.get(pid, 0.0) + 1.0 / (k + rank + 1)
        return sorted(scores.items(), key=lambda x: -x[1])

    def _rerank(self, q: str, cands: list[int]) -> dict[int, float]:
        if self._reranker is None:
            from fastembed.rerank.cross_encoder import TextCrossEncoder

            self._reranker = TextCrossEncoder(get_settings().rerank_model)
        docs = [self.by_id[c].text for c in cands]
        scores = list(self._reranker.rerank(q, docs, batch_size=32))
        return {c: float(s) for c, s in zip(cands, scores, strict=True)}

    def _signal(self, d: Doc) -> float:
        # Bayesian-ish rating (penalise tiny sample sizes) + log popularity, both in [0, 1].
        r = (d.rating * d.rating_count + 3.8 * 50) / (d.rating_count + 50) / 5.0
        pop = math.log1p(d.popularity) / math.log1p(self._max_pop)
        return 0.6 * r + 0.4 * pop

    # ---------------- public ----------------
    async def search(self, db: AsyncSession, query: str, filters: Filters | None = None,
                     k: int = 10, mode: str = "full", sort: str = "relevance") -> SearchResult:
        filters = filters or Filters()
        query = self.spell_correct(query)
        t: dict[str, float] = {}
        n = 60

        t0 = time.perf_counter()
        bm = [h for h in self._bm25(query, n * 2) if filters.match(self.by_id[h[0]])][:n]
        t["bm25"] = (time.perf_counter() - t0) * 1000

        dense: list[tuple[int, float]] = []
        if mode != "bm25":
            t0 = time.perf_counter()
            dense = [h for h in await self._dense(db, query, n * 3) if filters.match(self.by_id[h[0]])][:n]
            t["dense"] = (time.perf_counter() - t0) * 1000

        if mode == "bm25":
            fused = bm
        elif mode == "dense":
            fused = dense
        else:
            t0 = time.perf_counter()
            fused = self._rrf(bm, dense)
            t["rrf"] = (time.perf_counter() - t0) * 1000

        stages = {pid: {"fused": s} for pid, s in fused}
        ranked = [pid for pid, _ in fused]

        if mode in ("rerank", "full") and ranked and get_settings().rerank_enabled:
            t0 = time.perf_counter()
            top = ranked[:30]
            rr = await asyncio.to_thread(self._rerank, canonical_query(query), top)
            t["rerank"] = (time.perf_counter() - t0) * 1000
            for pid in top:
                stages[pid]["rerank"] = rr[pid]
            ranked = sorted(top, key=lambda p: -rr[p]) + ranked[30:]

        final: list[tuple[int, float]]
        if mode == "full":
            # Blend quality signals into the cross-encoder *logit*, not into sigmoid(logit). For weak paraphrase
            # queries every sigmoid is ~0, which erased relevance gaps and let popularity decide (measured:
            # recall@10 0.92 -> 0.81). In logit space a clear relevance gap always wins; rating/popularity
            # (signal in [0, 1]) only reorders near-ties. "best" gives quality a little more say.
            lam = SIGNAL_WEIGHT_BEST if sort == "best" else SIGNAL_WEIGHT
            final = []
            for pid in ranked[:30]:
                logit = stages[pid].get("rerank", 0.0)
                rel = 1 / (1 + math.exp(-logit))
                sig = self._signal(self.by_id[pid])
                stages[pid].update(relevance=rel, signal=sig)
                final.append((pid, logit + lam * sig))
            final.sort(key=lambda x: -x[1])
            # Drop clearly irrelevant tail once we have strong matches.
            if final and stages[final[0][0]]["relevance"] > 0.5:
                final = [f for f in final if stages[f[0]]["relevance"] > 0.02] or final
        else:
            final = [(pid, stages[pid].get("rerank", stages[pid]["fused"])) for pid in ranked]

        if sort == "price_low":
            final.sort(key=lambda x: self.by_id[x[0]].min_price)
        elif sort == "price_high":
            final.sort(key=lambda x: -self.by_id[x[0]].min_price)
        elif sort == "rating":
            final.sort(key=lambda x: -self.by_id[x[0]].rating)
        elif sort == "popular":
            final.sort(key=lambda x: -self.by_id[x[0]].popularity)

        t["total"] = sum(t.values())
        hits = [Hit(pid, round(s, 4), {k2: round(v, 4) for k2, v in stages[pid].items()})
                for pid, s in final[:k]]
        return SearchResult(hits=hits, timings_ms={k2: round(v, 2) for k2, v in t.items()}, mode=mode)

    async def warmup(self) -> None:
        """Load the cross-encoder before the first request instead of during it."""
        if self.docs and get_settings().rerank_enabled:
            await asyncio.to_thread(self._rerank, "milk", [self.docs[0].id])

    def bump_popularity(self, product_id: int, qty: int) -> None:
        if d := self.by_id.get(product_id):
            d.popularity += qty
            self._max_pop = max(self._max_pop, d.popularity)


search_engine = ProductSearch()

