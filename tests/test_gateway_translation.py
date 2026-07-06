"""Tests for Ollama/OpenAI gateway translation functions."""
import json
from metallama.app.ollama.routes.ollama import (
    _openai_tool_calls_to_ollama,
    _ollama_message_to_openai,
    _translate_options,
    _done_reason,
)


class FakeMessage:
    def __init__(self, role, content="", tool_calls=None, tool_call_id=None, tool_name=None, images=None):
        self.role = role
        self.content = content
        self.tool_calls = tool_calls or []
        self.tool_call_id = tool_call_id
        self.tool_name = tool_name
        self.images = images or []


def test_translate_options_maps_known_keys():
    result = _translate_options({"num_predict": 100, "temperature": 0.7})
    assert "max_tokens" in result
    assert result["max_tokens"] == 100


def test_translate_options_drops_unknown():
    result = _translate_options({"unknown_key": 123, "num_predict": 50})
    assert "unknown_key" not in result
    assert result["max_tokens"] == 50


def test_translate_options_none_returns_empty():
    assert _translate_options(None) == {}
    assert _translate_options({}) == {}


def test_openai_tool_calls_to_ollama_json_string_to_dict():
    calls = [{"function": {"name": "read_file", "arguments": '{"path": "/tmp"}'}}]
    result = _openai_tool_calls_to_ollama(calls)
    assert result[0]["function"]["name"] == "read_file"
    assert isinstance(result[0]["function"]["arguments"], dict)
    assert result[0]["function"]["arguments"]["path"] == "/tmp"


def test_openai_tool_calls_to_ollama_broken_json():
    calls = [{"function": {"name": "x", "arguments": "{broken json"}}]
    result = _openai_tool_calls_to_ollama(calls)
    assert result[0]["function"]["arguments"] == {"_raw": "{broken json"}


def test_openai_tool_calls_to_ollama_missing_id_preserved():
    calls = [{"function": {"name": "x", "arguments": "{}"}}]
    result = _openai_tool_calls_to_ollama(calls)
    assert result[0]["id"] is None


def test_ollama_message_to_openai_tool_message():
    m = FakeMessage(role="tool", content="result", tool_name="read_file", tool_call_id="call_1")
    result = _ollama_message_to_openai(m)
    assert result["role"] == "tool"
    assert result["tool_call_id"] == "call_1"
    assert result["name"] == "read_file"


def test_ollama_message_to_openai_assistant_tool_calls_args_dict_to_json():
    m = FakeMessage(
        role="assistant",
        tool_calls=[{"function": {"name": "calc", "arguments": {"x": 1}}}],
    )
    result = _ollama_message_to_openai(m)
    assert result["tool_calls"][0]["function"]["arguments"] == json.dumps({"x": 1})


def test_done_reason_mapping():
    assert _done_reason("tool_calls") == "tool_calls"
    assert _done_reason("length") == "length"
    assert _done_reason("stop") == "stop"
    assert _done_reason(None) == "stop"
    assert _done_reason("unknown") == "stop"
