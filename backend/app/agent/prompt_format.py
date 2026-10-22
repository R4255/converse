"""Qwen2.5 chat-template rendering and tool-call parsing, owned by the app.

Why: our fine-tuned model was trained on prompts rendered by the Hugging Face chat template. Ollama renders
the same messages slightly differently (e.g. compact JSON for tool schemas), and the fine-tuned model,
having specialised on one format, then emitted malformed tool calls (train/serve skew). Rendering the
prompt ourselves and calling Ollama in raw mode makes serving byte-identical to training.
This is a line-by-line port of the model's chat_template.jinja (verified equal in finetune/check_template.py).
"""

import json
import re

DEFAULT_SYSTEM = "You are Qwen, created by Alibaba Cloud. You are a helpful assistant."


def _tojson(x) -> str:
    return json.dumps(x, ensure_ascii=False)


def render_qwen(messages: list[dict], tools: list[dict] | None, add_generation_prompt: bool = True) -> str:
    out = []
    first_is_system = messages and messages[0]["role"] == "system"
    if tools:
        out.append("<|im_start|>system\n")
        out.append(messages[0]["content"] if first_is_system else DEFAULT_SYSTEM)
        out.append("\n\n# Tools\n\nYou may call one or more functions to assist with the user query.\n\n"
                   "You are provided with function signatures within <tools></tools> XML tags:\n<tools>")
        for tool in tools:
            out.append("\n" + _tojson(tool))
        out.append("\n</tools>\n\nFor each function call, return a json object with function name and arguments within "
                   "<tool_call></tool_call> XML tags:\n<tool_call>\n{\"name\": <function-name>, \"arguments\": "
                   "<args-json-object>}\n</tool_call><|im_end|>\n")
    else:
        out.append(f"<|im_start|>system\n{messages[0]['content'] if first_is_system else DEFAULT_SYSTEM}<|im_end|>\n")

    for i, m in enumerate(messages):
        role = m["role"]
        if role == "user" or (role == "system" and i != 0) or (role == "assistant" and not m.get("tool_calls")):
            out.append(f"<|im_start|>{role}\n{m.get('content') or ''}<|im_end|>\n")
        elif role == "assistant":
            out.append("<|im_start|>assistant")
            if m.get("content"):
                out.append("\n" + m["content"])
            for tc in m["tool_calls"]:
                fn = tc.get("function", tc)
                out.append('\n<tool_call>\n{"name": "' + fn["name"] + '", "arguments": ' + _tojson(fn.get("arguments") or {})
                           + "}\n</tool_call>")
            out.append("<|im_end|>\n")
        elif role == "tool":
            if i == 0 or messages[i - 1]["role"] != "tool":
                out.append("<|im_start|>user")
            out.append(f"\n<tool_response>\n{m['content']}\n</tool_response>")
            if i == len(messages) - 1 or messages[i + 1]["role"] != "tool":
                out.append("<|im_end|>\n")
    if add_generation_prompt:
        out.append("<|im_start|>assistant\n")
    return "".join(out)


_BLOCK = re.compile(r"<tool_call>\s*(.*?)\s*(?:</tool_call>|$)", re.S)


def _loads_lenient(blob: str) -> tuple[object, str]:
    """json.loads with small repairs for truncated objects. Returns (value or None, suffix that was appended)."""
    for extra in ("", "}", "}}", "]}", "]}}", "}]}}"):
        try:
            return json.loads(blob + extra), extra
        except json.JSONDecodeError:
            continue
    return None, ""


def parse_qwen_output(text: str) -> tuple[str, list[tuple[str, dict]], list[dict]]:
    """Split raw model output into (visible content, [(tool name, arguments)], [parse errors]).

    A <tool_call> block that can't be repaired is *reported*, never silently dropped: a dropped call is
    indistinguishable from "the model chose not to act", which hides model-format problems (this is how the
    fine-tuned model's train/serve skew first showed up: as "no tool called").
    """
    calls, errors = [], []
    for blob in _BLOCK.findall(text):
        obj, repair = _loads_lenient(blob)
        if not isinstance(obj, dict):
            errors.append({"reason": "invalid JSON in <tool_call>", "raw": blob[:300], "repaired": False})
        elif not obj.get("name"):
            errors.append({"reason": "tool call without a name", "raw": blob[:300], "repaired": False})
        else:
            calls.append((obj["name"], obj.get("arguments") or obj.get("parameters") or {}))
            if repair or "arguments" not in obj:
                # recovered, but still a format problem worth seeing in traces and eval metrics
                what = f"appended {repair!r}" if repair else "used 'parameters' instead of 'arguments'"
                errors.append({"reason": f"malformed tool call repaired ({what})", "raw": blob[:300], "repaired": True})
    content = _BLOCK.sub("", text).replace("<|im_end|>", "").strip()
    return content, calls, errors
