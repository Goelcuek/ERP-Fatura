"""Local language model backend over the OpenAI-compatible chat API.

Works with Ollama (http://127.0.0.1:11434/v1), llama.cpp's llama-server, LM Studio,
vLLM and similar servers running on the shop's own computer. No per-use cost.

Small models are imperfect at tool calling, so parsing is forgiving: <think> blocks are
removed, tool calls written as text (<tool_call>{...}</tool_call> or bare JSON) are
recovered, and arguments that are not valid JSON are reported back so the model can retry.
"""

import json
import re
import uuid
from dataclasses import dataclass, field

import requests

THINK = re.compile(r"<think>.*?</think>\s*", re.S)
TEXT_TOOL_CALL = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)


class LLMError(Exception):
    pass


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict = None  # None when the model sent unparseable arguments
    raw_arguments: str = ""


@dataclass
class Turn:
    text: str = ""
    tool_calls: list = field(default_factory=list)
    usage: dict = field(default_factory=dict)

    def history_message(self):
        """The assistant message to append to the conversation (OpenAI format)."""
        msg = {"role": "assistant", "content": self.text or ""}
        if self.tool_calls:
            msg["tool_calls"] = [{"id": c.id, "type": "function",
                                  "function": {"name": c.name, "arguments": c.raw_arguments or "{}"}}
                                 for c in self.tool_calls]
        return msg


def _loads(s):
    if isinstance(s, dict):
        return s
    s = (s or "").strip()
    if not s:
        return {}
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s)
    try:
        v = json.loads(s)
        return v if isinstance(v, dict) else None
    except ValueError:
        return None


def parse_message(message):
    """Turn an OpenAI-format assistant message into a Turn."""
    content = message.get("content") or ""
    if isinstance(content, list):  # some servers return content parts
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    content = THINK.sub("", content)
    content = re.sub(r"^.*?</think>\s*", "", content, flags=re.S)  # unterminated opening tag
    calls = []
    for tc in message.get("tool_calls") or []:
        fn = tc.get("function") or {}
        raw = fn.get("arguments")
        raw_s = raw if isinstance(raw, str) else json.dumps(raw or {}, ensure_ascii=False)
        calls.append(ToolCall(id=tc.get("id") or "call_" + uuid.uuid4().hex[:12], name=fn.get("name", ""),
                              arguments=_loads(raw), raw_arguments=raw_s))
    if not calls:
        found = TEXT_TOOL_CALL.findall(content)
        if not found:
            bare = content.strip()
            if bare.startswith("{") and '"name"' in bare and ('"arguments"' in bare or '"parameters"' in bare):
                found = [bare]
        for blob in found:
            data = _loads(blob) or {}
            name = data.get("name")
            if name:
                args = data.get("arguments", data.get("parameters", {}))
                calls.append(ToolCall(id="call_" + uuid.uuid4().hex[:12], name=name, arguments=_loads(args),
                                      raw_arguments=json.dumps(args, ensure_ascii=False)))
        if calls:
            content = TEXT_TOOL_CALL.sub("", content)
            if content.strip().startswith("{"):
                content = ""
    return Turn(text=content.strip(), tool_calls=calls)


# servers that rejected reasoning_effort (older Ollama, some other servers): don't send it again
_NO_REASONING_PARAM = set()


class OpenAICompatibleBackend:
    def __init__(self, base_url, model, api_key="", timeout=120, temperature=0.2, thinking=True, session=None):
        self.base_url = (base_url or "").rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.temperature = temperature
        self.thinking = thinking
        self.http = session or requests.Session()

    def _headers(self):
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def chat(self, messages, tools):
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "stream": False,
        }
        if tools:
            body["tools"] = [{"type": "function", "function": t} for t in tools]
            body["tool_choice"] = "auto"
        if not self.thinking and self.base_url not in _NO_REASONING_PARAM:
            body["reasoning_effort"] = "none"  # Ollama: turns the model's thinking off (answers much sooner)
        resp = self._post(body)
        if resp.status_code == 400 and "reasoning_effort" in body and "reason" in resp.text.lower():
            _NO_REASONING_PARAM.add(self.base_url)  # server doesn't know the switch: think as usual
            del body["reasoning_effort"]
            resp = self._post(body)
        if resp.status_code == 404:
            raise LLMError(f"Model “{self.model}” was not found on the model server. Check the model name in Settings.")
        if resp.status_code >= 400:
            raise LLMError(f"Model server error {resp.status_code}: {resp.text[:200]}")
        try:
            data = resp.json()
            message = data["choices"][0]["message"]
        except (ValueError, KeyError, IndexError):
            raise LLMError("Unexpected answer from the model server.")
        turn = parse_message(message)
        turn.usage = data.get("usage") or {}
        return turn

    def _post(self, body):
        try:
            return self.http.post(f"{self.base_url}/chat/completions", json=body, headers=self._headers(),
                                  timeout=self.timeout)
        except requests.Timeout:
            raise LLMError("The local AI model took too long to answer.")
        except requests.RequestException:
            raise LLMError("The local AI model is not running. Start Ollama (or your model server) and try again.")

    def health(self):
        """Returns (ok, message). Checks that the server answers and the model is installed."""
        try:
            resp = self.http.get(f"{self.base_url}/models", headers=self._headers(), timeout=10)
        except requests.RequestException:
            return False, "The local AI model is not running. Start Ollama (or your model server) and try again."
        if resp.status_code >= 400:
            return False, f"Model server error {resp.status_code}"
        try:
            ids = [m.get("id") for m in resp.json().get("data", [])]
        except ValueError:
            ids = []
        if ids and self.model not in ids:
            return False, f"Model “{self.model}” is not installed. Available: {', '.join(i for i in ids if i)}"
        return True, f"Model server is running and “{self.model}” is available."
