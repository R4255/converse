COMPOSE = podman-compose -f infra/compose.yaml
BE = cd backend &&

.PHONY: infra seed api worker web dev test evals eval-retrieval up down mcp lint

infra:            ## Postgres (pgvector) + LocalStack (SQS, S3, EventBridge Scheduler)
	$(COMPOSE) up -d postgres localstack

seed:             ## catalog, users, a year of orders, embeddings
	$(BE) uv run python -m app.seed

api:
	$(BE) uv run uvicorn app.main:app --reload --port 8000

worker:
	$(BE) uv run python -m app.worker

web:
	cd frontend && npm run dev

mcp:              ## MCP server over stdio
	$(BE) uv run python -m app.mcp_server

lint:
	$(BE) uv run ruff check app evals tests
	cd frontend && npx tsc -b

test:
	$(BE) uv run pytest -q

evals:            ## full agent eval suite (needs Ollama)
	$(BE) uv run python -m evals.run_agent --tag $${TAG:-dev}

eval-retrieval:
	$(BE) uv run python -m evals.run_retrieval

up:               ## everything in containers (Ollama stays on the host for GPU access)
	$(COMPOSE) --profile app up -d --build

down:
	$(COMPOSE) --profile app down
