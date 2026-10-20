"""Ollama chat client with native tool calling (+ a fallback parser for models that emit
tool calls as text)."""

import json
import re
import time
from dataclasses import dataclass, field

import httpx

from app.config import get_settings


@dataclass
class ToolCall:
    name: str
    arguments: dict


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[ToolCall]
    prompt_tokens: int
    completion_tokens: int
    latency_ms: float
    raw_message: dict = field(default_factory=dict)


class LLMError(Exception):
    pass


_TEXT_CALL = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)


_PY_CALL = re.compile(r"^\s*`*([a-z_]+)\((.*)\)`*\s*$", re.S)
_PY_ARG = re.compile(r"(\w+)\s*=\s*(\"[^\"]*\"|'[^']*'|-?\d+(?:\.\d+)?|True|False|None)")


def _parse_python_call(content: str, known: set[str]) -> list[ToolCall]:
    """Recover `navigate(page="home")`-style calls that small models sometimes write as text."""
    m = _PY_CALL.match(content)
    if not m or m[1] not in known:
        return []
    consts = {"True": True, "False": False, "None": None}
    args = {}
    for k, v in _PY_ARG.findall(m[2]):
        if v[0] in "\"'":
            args[k] = v[1:-1]
        elif v in consts:
            args[k] = consts[v]
        else:
            args[k] = float(v) if "." in v else int(v)
    return [ToolCall(m[1], args)]


def _parse_text_calls(content: str, known: set[str] | None = None) -> list[ToolCall]:
    if known and (py := _parse_python_call(content, known)):
        return py
    blobs = _TEXT_CALL.findall(content)
    if not blobs and content.strip().startswith("{") and '"name"' in content:
        blobs = [content.strip()]
    calls = []
    for b in blobs:
        try:
            obj = json.loads(b)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "name" in obj:
            calls.append(ToolCall(obj["name"], obj.get("arguments") or obj.get("parameters") or {}))
    return calls


# Every request must use identical load-time options (num_ctx in particular); a mismatch makes
# Ollama reload the whole model, which costs 5-25 s.
NUM_CTX = 8192


class OllamaChat:
    def __init__(self, model: str | None = None) -> None:
        s = get_settings()
        self.model = model or s.agent_model
        self._client = httpx.AsyncClient(base_url=s.ollama_url, timeout=s.llm_timeout_s)

    @property
    def raw_mode(self) -> bool:
        raw = {m.strip() for m in get_settings().raw_prompt_models.split(",") if m.strip()}
        return self.model in raw or self.model.split(":")[0] in raw

    async def _chat_raw(self, messages: list[dict], tools: list[dict] | None, max_tokens: int) -> LLMResponse:
        """Render the prompt ourselves (byte-identical to training) and parse tool calls ourselves."""
        from app.agent.prompt_format import parse_qwen_output, render_qwen

        body = {"model": self.model, "prompt": render_qwen(messages, tools), "raw": True, "stream": False,
                "keep_alive": "30m", "options": {"temperature": 0, "num_ctx": NUM_CTX, "num_predict": max_tokens,
                                                 "seed": 7, "stop": ["<|im_end|>"]}}
        t0 = time.perf_counter()
        try:
            r = await self._client.post("/api/generate", json=body)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise LLMError(f"ollama request failed: {e}") from e
        data = r.json()
        content, calls = parse_qwen_output(data.get("response", ""))
        return LLMResponse(content=content, tool_calls=[ToolCall(n, a) for n, a in calls],
                           prompt_tokens=data.get("prompt_eval_count", 0), completion_tokens=data.get("eval_count", 0),
                           latency_ms=(time.perf_counter() - t0) * 1000, raw_message={"response": data.get("response")})

    async def chat(self, messages: list[dict], tools: list[dict] | None = None, max_tokens: int = 300) -> LLMResponse:
        if self.raw_mode:
            return await self._chat_raw(messages, tools, max_tokens)
        body = {"model": self.model, "messages": messages, "stream": False, "keep_alive": "30m",
                "options": {"temperature": 0, "num_ctx": NUM_CTX, "num_predict": max_tokens, "seed": 7}}
        if tools:
            body["tools"] = tools
        t0 = time.perf_counter()
        try:
            r = await self._client.post("/api/chat", json=body)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise LLMError(f"ollama request failed: {e}") from e
        data = r.json()
        msg = data.get("message", {})
        calls = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {})
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {"__raw__": args}
            calls.append(ToolCall(fn.get("name", ""), args))
        content = msg.get("content") or ""
        if not calls and content:
            known = {t["function"]["name"] for t in tools or []}
            calls = _parse_text_calls(content, known)
            if calls:
                content = ""
        return LLMResponse(content=content.strip(), tool_calls=calls,
                           prompt_tokens=data.get("prompt_eval_count", 0),
                           completion_tokens=data.get("eval_count", 0),
                           latency_ms=(time.perf_counter() - t0) * 1000, raw_message=msg)

    async def warmup(self) -> None:
        try:
            await self._client.post("/api/chat", json={"model": self.model, "messages": [], "keep_alive": "30m",
                                                       "options": {"num_ctx": NUM_CTX}})
        except httpx.HTTPError:
            pass
