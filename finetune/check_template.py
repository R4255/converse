"""Verifies the app's Python port of the Qwen chat template is byte-identical to the HF tokenizer's rendering.

    cd finetune && uv run python check_template.py
"""

import importlib.util
import json
from pathlib import Path

from transformers import AutoTokenizer

spec = importlib.util.spec_from_file_location(
    "prompt_format", Path(__file__).resolve().parents[1] / "backend/app/agent/prompt_format.py")
pf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pf)

tok = AutoTokenizer.from_pretrained("Qwen/Qwen2.5-1.5B-Instruct")
mismatch = n = 0
for path in ("data/train.jsonl", "data/valid.jsonl"):
    for line in open(path):
        r = json.loads(line)
        msgs = r["messages"][:-1]
        hf = tok.apply_chat_template(msgs, tools=r["tools"], tokenize=False, add_generation_prompt=True)
        ours = pf.render_qwen(msgs, r["tools"])
        n += 1
        if hf != ours:
            mismatch += 1
            if mismatch == 1:
                i = next(k for k in range(min(len(hf), len(ours))) if hf[k] != ours[k])
                print("first diff at", i, repr(hf[i - 60:i + 60]), "\nvs", repr(ours[i - 60:i + 60]))
print(f"{n} prompts compared, {mismatch} mismatches")
