# Converse: an AI command layer for a grocery app

One input box that can do anything the app can do. *"Show me my purchase of 5th January"*,
*"add my usual milk and brown bread, then take me to checkout"*, *"best pizza ingredients"*, *"reorder what I
bought last Friday"*. The agent searches, fills the cart and navigates the UI, and it **always asks before
anything irreversible**.

Everything runs locally: open models on Ollama (a 7B, plus a **1.5B I fine-tuned with LoRA to match a 3B**),
Postgres + pgvector, and AWS services (SQS, S3, EventBridge Scheduler) on LocalStack under Podman.

![checkout](docs/img/04-checkout.png)

## Results

All numbers come from the eval harness in [`backend/evals`](backend/evals): 130 tasks / 143 turns in 11
categories, end-state assertions, isolated database, Apple M2 16 GB. The full history, including regressions and
test changes, is in [docs/EVAL_LOG.md](docs/EVAL_LOG.md).

**Agent: iterating on failures found in traces**

| run | task success | what changed |
|---|---|---|
| baseline | 74.4% | first end-to-end version |
| v2 | 87.2% | cart in context, ambiguity policy, argument grounding, filter inference |
| v3 | 87.7% | delivery slots, spelling, habits, verifiers; *one prompt rule regressed search* |
| v4 | 90.0% | precise add-vs-search rules, model-reload bug fixed, must-act verifier |
| **v6** | **93.1%** | search ranking fix, five bugs from v5 traces (p50 6.2 s, p95 13.3 s, safety 100%) |

**Models and ablations** (same code and tasks; latency varies ±30% with machine memory state)

| config | task success | p50 | LLM calls/turn |
|---|---|---|---|
| qwen2.5 7B instruct (v4) | **90.0%** | 9.1 s | 1.46 |
| … router off | 86.2% | 5.5 s | 1.55 |
| … early exit off | 90.0% | 9.4 s (p95 27.3 s vs 19.9 s) | 1.85 |
| qwen2.5-coder 7B | 78.5% | 8.6 s | 1.51 |
| qwen2.5 3B instruct | 75.4% | 4.1 s | 1.48 |
| **converse-1.5b (fine-tuned)** | **75.4%** | **1.4 s** | 2.39 |
| converse-1.5b served with Ollama's template | 62.3% | 3.4 s | 1.90 |
| qwen2.5 1.5B instruct | 60.0% | 2.3 s | 1.52 |

* **Fine-tuning:** LoRA on 2,400 code-labelled examples (eval held out) took the 1.5B model from 60.0% to
  **75.4%, matching the 3B at the lowest latency**. The first fine-tuned eval scored 62.3% because of
  **train/serve skew**: Ollama renders tool schemas as compact JSON, the fine-tuned model imitated that style and
  emitted malformed calls, and they were silently dropped. The fix renders prompts app-side, byte-identical to
  training on all 2,560 prompts. See [finetune/README.md](finetune/README.md).
* **The deterministic router improves accuracy, not just speed** (90.0% vs 86.2%).
* **Template early exit** saves 21% of LLM calls at equal accuracy.

**Search: retrieval ablation** (71 labelled queries; ground truth from catalog structure)

| stage | Recall@10 | nDCG@10 | MRR@10 | p50 |
|---|---|---|---|---|
| BM25 | 0.817 | 0.795 | 0.841 | 0.1 ms |
| dense (pgvector) | 0.895 | 0.855 | 0.887 | 27 ms |
| hybrid (RRF) | 0.921 | 0.876 | 0.899 | 28 ms |
| + cross-encoder rerank | 0.922 | 0.890 | 0.908 | 157 ms |
| + rating/popularity, sigmoid blend (first version) | 0.806 | 0.790 | 0.864 | 152 ms |
| **+ rating/popularity, logit blend (shipped)** | **0.887** | **0.868** | **0.910** | 156 ms |

## What's interesting here

* **The agent drives the UI.** Tools return typed `ui_actions` (`navigate`, `show_products`, `confirm`,
  `clarify`, …) that the React app executes, streamed over SSE with live step status.
* **Determinism where small models are weak.** The model passes the user's words, and code resolves them:
  dates (`"last friday"` → a date range), products (`"my usual milk"` uses purchase history; `"#2"` refers to the
  screen; `"big pack"` → largest size) and cron expressions. Arguments the user never said are dropped before
  execution (*argument grounding*).
* **Safety is structural.** Placing orders, emptying the cart and large quantities create single-use,
  user-bound, expiring confirmation tokens; the LLM can't execute them. A stale-cart guard refuses to charge
  an amount the user didn't see. Planted prompt-injection reviews are redacted and covered by evals.
* **An eval harness that checks outcomes.** 130 tasks / 143 turns across 11 categories, asserting end state
  (cart contents, page opened, cards shown) rather than exact tool arguments. Every change is a measured
  before/after; see [docs/EVAL_LOG.md](docs/EVAL_LOG.md).
* **Latency engineering from traces.** A KV-cache-friendly prompt layout, a deterministic fast-path router,
  template early exit and startup warmup. Every turn is traced (LLM calls, tool calls, tokens, ms) and viewable
  in the app.
* **Production-shaped backend.** Idempotency keys, `SELECT … FOR UPDATE` stock locking, a transactional
  outbox → SQS → idempotent worker (DLQ after 5 receives), and EventBridge Scheduler reminders.
* **Fine-tuned small model, served in its training format.** A 1.5B distilled with LoRA on Apple MLX, exported to
  GGUF for Ollama, and served with app-side prompt rendering to avoid train/serve skew.
* **Delivery scheduling.** "Deliver tomorrow around 5 am" resolves to a real slot ("we deliver from 6 AM, so I
  booked 6–7 AM") instead of being silently ignored.
* **MCP server.** The same tools, schemas and confirmation gates are exposed to Claude Desktop, Cursor and
  other MCP clients; the confirmation tool is annotated `destructive`.

Design rationale and the failures found along the way: [docs/DESIGN.md](docs/DESIGN.md).

## Architecture

```
React + antd ──SSE──► FastAPI /api/agent/turn
  ⌘K command bar         │
  assistant panel        ├─ fast-path router (no LLM for "open cart", "place my order")
  executes ui_actions    └─ agent loop ──► Ollama qwen2.5:7b-instruct (native tool calling)
                                  │
                 12 typed tools ──┼─ resolvers: dates · products (screen / history / search) · sizes · cron
                                  ├─ hybrid search: BM25 + pgvector → RRF → cross-encoder → rating/popularity
                                  └─ domain services ──► Postgres (pgvector)
                                                          │ transactional outbox
                                      LocalStack: SQS ◄───┘ ──► worker ──► notifications, popularity
                                                  EventBridge Scheduler ──► SQS reminders
```

## Run it

Prerequisites: Podman (machine running), [Ollama](https://ollama.com), Python via [uv](https://docs.astral.sh/uv/), Node 20+.

```bash
ollama pull qwen2.5:7b-instruct && ollama pull nomic-embed-text
make infra     # Postgres + LocalStack
make seed      # catalog, users, a year of orders, embeddings (~10 s)
make api       # :8000      (separate terminals)
make worker
make web       # http://localhost:5173
```

Everything in containers (Ollama stays on the host for GPU access): `make up`.

| | |
|---|---|
| `make test` | 67 tests: unit, resolver regressions, integration (idempotency, locking, outbox, confirmation tokens), MCP over stdio |
| `make evals` | agent eval suite; `TAG=x` names the run. Use `--model`, `--no-router`, `--no-early-exit` for ablations |
| `make eval-retrieval` | retrieval ablation |
| `make mcp` | MCP server over stdio |
| `node frontend/scripts/e2e.mjs` | headless browser end-to-end run + screenshots |

To use the fine-tuned model instead: build it with [finetune/README.md](finetune/README.md), then
`AGENT_MODEL=converse-1.5b make api`.

New to these topics? [docs/LEARNING_GUIDE.md](docs/LEARNING_GUIDE.md) explains every concept, linked to the code.

## Screenshots

| | |
|---|---|
| ![](docs/img/02-order-lookup.png) "show me my purchase of 5th january" | ![](docs/img/03-search.png) "best pizza ingredients" |
| ![](docs/img/05-confirm.png) "place my order" → confirmation | ![](docs/img/07-trace.png) per-turn trace |

## Limitations

* Synthetic catalog and order history (340 products, 3 users), designed to give the evals real ground truth.
* Single-node and local; the numbers above come from one M2 laptop, and latency varies with its memory state.
* The fine-tuned model was trained for only 150 steps (~0.13 epoch) because of laptop training speed (~33 s/step).
* Model weights aren't in the repo (too large); they're reproducible from `finetune/`.
* The eval suite was written by the same person who built the agent; failures are analysed in the open in
  `docs/EVAL_LOG.md`, including test changes.
