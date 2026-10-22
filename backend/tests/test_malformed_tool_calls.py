"""Regression fixtures for the train/serve-skew incident (see docs/EVAL_LOG.md).

The fine-tuned model, served with a mismatched prompt format, emitted the malformed tool calls below. They were
silently dropped, which made a *format* problem look like *the model chose not to act*. These tests pin the
contract: malformed calls are either repaired *and flagged*, or reported as explicit parse failures. Never silent.
"""

import json

import httpx
import pytest

from app.agent.llm import OllamaChat
from app.agent.prompt_format import parse_qwen_output

pytestmark = pytest.mark.asyncio(loop_scope="session")

# Verbatim outputs captured from converse-1.5b during the incident (compact JSON imitating Ollama's template).
MISSING_BRACE = ('<tool_call>\n{"name":"add_to_cart","arguments":{"items":[{"product":"maggi masala noodles",'
                 '"quantity":2,"size":"packs"}]}\n</tool_call>')
WRONG_KEY = '<tool_call>\n{"name":"search_products","parameters":{"query":"chips", "sort":"price_low"}}\n</tool_call>'
# Not repairable: missing comma between fields.
UNREPAIRABLE = '<tool_call>\n{"name": "add_to_cart", "arguments": {"items": [{"product": "milk" "quantity": 2}]}}\n</tool_call>'


def test_repaired_calls_are_flagged_not_silent():
    for text, name in ((MISSING_BRACE, "add_to_cart"), (WRONG_KEY, "search_products")):
        _, calls, errors = parse_qwen_output(text)
        assert [c[0] for c in calls] == [name]
        assert len(errors) == 1 and errors[0]["repaired"] is True


def test_unrepairable_call_is_an_explicit_parse_failure():
    content, calls, errors = parse_qwen_output(UNREPAIRABLE)
    assert calls == [] and content == ""
    assert errors and errors[0]["repaired"] is False and "invalid JSON" in errors[0]["reason"]


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://ollama.test")


async def test_serving_path_raw_mode_reports_parse_failure():
    """Through the real client in raw mode (how the fine-tuned model is served)."""
    llm = OllamaChat("converse-1.5b")
    assert llm.raw_mode
    llm._client = _client(lambda req: httpx.Response(200, json={"response": UNREPAIRABLE, "eval_count": 30}))
    resp = await llm.chat([{"role": "user", "content": "add 2 milk"}], tools=[])
    assert resp.tool_calls == [] and resp.parse_errors and not resp.parse_errors[0]["repaired"]


async def test_serving_path_server_side_drop_is_reported():
    """The original symptom: tokens generated, but the server's parser returned no content and no tool calls."""
    llm = OllamaChat("qwen2.5:1.5b-instruct")
    assert not llm.raw_mode
    body = {"message": {"role": "assistant", "content": ""}, "eval_count": 42, "prompt_eval_count": 1500}
    llm._client = _client(lambda req: httpx.Response(200, json=body))
    resp = await llm.chat([{"role": "user", "content": "add 2 milk"}], tools=[])
    assert resp.tool_calls == []
    assert resp.parse_errors and "42 tokens" in resp.parse_errors[0]["reason"]


def test_fixture_is_valid_json_once_repaired():
    # documents what the model *meant*, so the fixture stays readable
    assert json.loads(MISSING_BRACE.split("\n")[1] + "}")["arguments"]["items"][0]["quantity"] == 2
