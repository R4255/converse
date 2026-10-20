#!/bin/bash
# Runs the full comparison matrix on the isolated eval database, one config at a time.
#   bash evals/run_matrix.sh            (from backend/; ~2 h on an M2)
# Each run writes evals/results/agent-<tag>.json; the retrieval ablation writes retrieval.json.
set -u
cd "$(dirname "$0")/.."
export DATABASE_URL=postgresql+asyncpg://converse:converse@localhost:5433/converse_eval

run() {  # tag, model, extra flags...
  local tag=$1 model=$2; shift 2
  echo "=== $(date +%H:%M) $tag ($model $*)"
  uv run python -m evals.run_agent --tag "$tag" --model "$model" "$@" 2>&1 | grep -E "FAIL|task_success|wrote|Error"
  ollama stop "$model" >/dev/null 2>&1   # free memory before the next model loads
}

echo "=== $(date +%H:%M) retrieval ablation"
uv run python -m evals.run_retrieval 2>&1 | grep -E "mode|wrote|Error"

run v4                qwen2.5:7b-instruct
run ft-converse-1.5b  converse-1.5b
run base-qwen-1.5b    qwen2.5:1.5b-instruct
run base-qwen-3b      qwen2.5:3b-instruct
run coder-7b          qwen2.5-coder:7b
run v4-no-router      qwen2.5:7b-instruct --no-router
run v4-no-early-exit  qwen2.5:7b-instruct --no-early-exit
echo "=== $(date +%H:%M) done"
