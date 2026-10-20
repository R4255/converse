"""Text embeddings via the local Ollama server (nomic-embed-text, 768-d).

nomic-embed-text is trained with task prefixes; omitting them noticeably hurts retrieval.
"""

import httpx

from app.config import get_settings

_PREFIX = {"document": "search_document: ", "query": "search_query: "}


async def embed(texts: list[str], kind: str = "query", batch: int = 64) -> list[list[float]]:
    s = get_settings()
    out: list[list[float]] = []
    async with httpx.AsyncClient(base_url=s.ollama_url, timeout=120) as client:
        for i in range(0, len(texts), batch):
            chunk = [_PREFIX[kind] + t for t in texts[i : i + batch]]
            r = await client.post("/api/embed", json={"model": s.embed_model, "input": chunk})
            r.raise_for_status()
            out.extend(r.json()["embeddings"])
    return out


def product_document(p) -> str:
    """The text we index for a product (shared by BM25 and dense retrieval)."""
    tags = " ".join(p.tags or [])
    return f"{p.name}. {p.category} > {p.subcategory}. {p.description} {tags}"
