FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project
COPY app ./app
COPY evals ./evals
# Pre-download the cross-encoder so the first request isn't slow.
RUN uv run --no-sync python -c "from fastembed.rerank.cross_encoder import TextCrossEncoder as T; T('Xenova/ms-marco-MiniLM-L-6-v2')"
EXPOSE 8000
CMD ["uv", "run", "--no-sync", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
