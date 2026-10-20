# Fine-tuning: distilling the agent into a 1.5B model

Goal: a small model that makes the agent's tool calls nearly as well as the 7B, at a fraction of the latency
and memory, so it can be served on cheap hardware.

## Pipeline

```
gen_data.py ──► data/{train,valid}.jsonl ──► mlx_lm.lora (LoRA) ──► adapters/ ──► mlx_lm.fuse ──► fused/
                                                                                              │
                         Ollama "converse-1.5b" ◄── ollama create ◄── convert_hf_to_gguf.py (q8_0) ◄┘
```

1. **Data** ([gen_data.py](gen_data.py)): 2,400 train / 160 validation examples, rendered with the agent's
   *exact* system prompt, tool schemas and per-turn context. Labels come from code templates (correct by
   construction), covering single and multi-tool calls, on-screen references, delivery times, reminders,
   refusals, two-step search-then-add, and "repeated request" turns (the history-anchoring failure).
   Any request with fuzzy similarity ≥ 85 to an eval utterance is dropped, so `evals/` stays held out.
2. **Train** ([lora.yaml](lora.yaml)): Qwen2.5-1.5B-Instruct, LoRA rank 16 on all attention + MLP projections
   of all 28 layers (18.5M trainable parameters, 1.2%). Prompt tokens are masked, so the loss covers only the
   assistant's tool calls or reply.
3. **Merge, convert, serve:** fuse the adapter into the base weights, export GGUF (q8_0), and create an Ollama
   model whose chat template is copied from the base `qwen2.5:1.5b-instruct` ([Modelfile](Modelfile)).

## Run

```bash
cd finetune
uv sync
(cd ../backend && uv run python ../finetune/gen_data.py)                 # regenerate data
ollama stop qwen2.5:7b-instruct                                           # free memory
caffeinate -i uv run mlx_lm.lora -c lora.yaml                             # train; Ctrl+C is safe (checkpoints every 150 steps)
uv run mlx_lm.fuse --model Qwen/Qwen2.5-1.5B-Instruct --adapter-path adapters --save-path fused --dequantize
git clone --depth 1 --filter=blob:none --sparse https://github.com/ggml-org/llama.cpp.git
(cd llama.cpp && git sparse-checkout set --no-cone /convert_hf_to_gguf.py /gguf-py /conversion)
uv pip install ./llama.cpp/gguf-py torch sentencepiece          # converter deps (not in pyproject: local path)
uv run python llama.cpp/convert_hf_to_gguf.py fused --outfile converse-1.5b-q8_0.gguf --outtype q8_0
ollama create converse-1.5b -f Modelfile
```

Use it in the app: `AGENT_MODEL=converse-1.5b make api`. Evaluate it:
`cd ../backend && uv run python -m evals.run_agent --tag ft --model converse-1.5b`.

## What happened in training (Apple M2, 16 GB)

| | |
|---|---|
| Validation loss | 0.750 (step 1) → **0.028** (step 150) |
| Speed | ~33 s/step (every example carries ~2.2k prompt tokens of tool schemas) |
| Stopped at | step 150 of 900 (~0.13 epoch), because validation loss had already dropped 96% |
| Output | 74 MB LoRA adapter → 1.6 GB q8_0 GGUF |

Validation loss on synthetic data only shows that the model learned the *format*. Whether it generalises
is measured by the held-out agent eval (results in the main README).

## Deploying only the model

The app talks to any Ollama server, so deploying the model means running Ollama somewhere with this one file:

```bash
# on the server
ollama create converse-1.5b -f Modelfile     # Modelfile + converse-1.5b-q8_0.gguf copied alongside
# in the app's environment
OLLAMA_URL=http://<server>:11434  AGENT_MODEL=converse-1.5b
```

Model files don't belong in git. Host the GGUF on the Hugging Face Hub
(`huggingface-cli upload <user>/converse-1.5b converse-1.5b-q8_0.gguf Modelfile`) or in S3, and download them on the server.
