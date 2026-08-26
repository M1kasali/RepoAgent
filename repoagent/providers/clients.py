"""模型后端适配层。

runtime 只关心一件事：给我一个 prompt，我拿回一段文本。
不同 provider 在 HTTP 接口、响应结构、是否支持 prompt cache 上都有差异，
这些差异都在这里被抹平成统一的 complete() 接口。
"""

import json
import time
from http.client import RemoteDisconnected
import urllib.error
import urllib.request

import json_repair

from .base import (
    CancellationToken,
    ModelEvent,
    ModelMessage,
    ModelRequest,
    ModelResult,
    ModelUsage,
    ProviderCancelledError,
    ProviderConnectionError,
    ProviderError,
    ProviderProtocolError,
    ProviderTimeoutError,
    ToolCall,
)

OPENAI_COMPATIBLE_USER_AGENT = "repoagent/0.1"


def _provider_name(client) -> str:
    profile = getattr(client, "profile", None)
    return str(getattr(profile, "provider", "") or type(client).__name__)


def _request_messages(request: ModelRequest) -> tuple[ModelMessage, ...]:
    return request.messages or (ModelMessage(role="user", content=request.prompt),)


def _openai_response_input(request: ModelRequest) -> list[dict]:
    items = []
    for index, message in enumerate(_request_messages(request)):
        if message.role in {"system", "user"}:
            items.append(
                {
                    "role": message.role,
                    "content": [{"type": "input_text", "text": message.content}],
                }
            )
        elif message.role == "assistant":
            for block in message.thinking_blocks:
                block = dict(block)
                if block.get("type") != "reasoning":
                    continue
                replay = {
                    key: block[key]
                    for key in (
                        "type",
                        "id",
                        "status",
                        "summary",
                        "encrypted_content",
                    )
                    if key in block
                }
                # Responses reasoning items are opaque server-issued values. Do not
                # fabricate an input item from a display-only reasoning summary.
                if replay.get("id") or replay.get("encrypted_content"):
                    items.append(replay)
            if message.content:
                items.append(
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": message.content}],
                        "status": "completed",
                        "id": f"msg_{index}",
                    }
                )
            for call in message.tool_calls:
                items.append(
                    {
                        "type": "function_call",
                        "id": f"fc_{index}",
                        "call_id": call.id,
                        "name": call.name,
                        "arguments": json.dumps(
                            dict(call.arguments), sort_keys=True, separators=(",", ":")
                        ),
                    }
                )
        else:
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": message.tool_call_id,
                    "output": message.content,
                }
            )
    return items


def _anthropic_messages(request: ModelRequest) -> list[dict]:
    projected = []

    def append(role, blocks):
        if projected and projected[-1]["role"] == role:
            projected[-1]["content"].extend(blocks)
        else:
            projected.append({"role": role, "content": blocks})

    for message in _request_messages(request):
        if message.role == "system":
            append("user", [{"type": "text", "text": message.content}])
        elif message.role in {"user", "assistant"}:
            blocks = []
            if message.role == "assistant":
                blocks.extend(dict(block) for block in message.thinking_blocks)
            if message.content:
                blocks.append({"type": "text", "text": message.content})
            if message.role == "assistant":
                blocks.extend(
                    {
                        "type": "tool_use",
                        "id": call.id,
                        "name": call.name,
                        "input": dict(call.arguments),
                    }
                    for call in message.tool_calls
                )
            append(message.role, blocks)
        else:
            block = {
                "type": "tool_result",
                "tool_use_id": message.tool_call_id,
                "content": message.content or "(empty)",
            }
            append("user", [block])
    return projected


def _usage_source(values, *keys):
    return "actual" if any(values.get(key) is not None for key in keys) else "missing"


def _raise_if_cancelled(token: CancellationToken | None, provider: str) -> None:
    if token is not None:
        token.raise_if_cancelled(provider=provider)


def _cancelled_during_io(
    token: CancellationToken | None, provider: str, exc: BaseException
) -> None:
    if token is not None and token.cancelled:
        raise ProviderCancelledError(
            "model request was cancelled", provider=provider
        ) from exc


class _TypedModelClient:
    """Expose the typed provider contract while preserving complete()."""

    def generate(self, request: ModelRequest) -> ModelResult:
        started_at = time.monotonic()
        _raise_if_cancelled(request.cancellation_token, type(self).__name__)
        try:
            text = self.complete(
                request.prompt,
                request.max_output_tokens,
                prompt_cache_key=request.prompt_cache_key,
                prompt_cache_retention=request.prompt_cache_retention,
                timeout=request.timeout_seconds,
                tools=request.tools,
                cancellation_token=request.cancellation_token,
            )
        except TimeoutError as exc:
            raise ProviderTimeoutError(
                f"{type(self).__name__} request timed out",
                provider=type(self).__name__,
            ) from exc
        _raise_if_cancelled(request.cancellation_token, type(self).__name__)
        metadata = dict(getattr(self, "last_completion_metadata", {}) or {})
        return ModelResult(
            text=str(text),
            tool_calls=tuple(getattr(self, "last_tool_calls", ()) or ()),
            reasoning_content=str(getattr(self, "last_reasoning_content", "") or ""),
            thinking_blocks=tuple(getattr(self, "last_thinking_blocks", ()) or ()),
            finish_reason=str(getattr(self, "last_finish_reason", "stop")),
            usage=ModelUsage.from_metadata(metadata),
            provider=_provider_name(self),
            model=str(getattr(self, "model", "")),
            latency_ms=int((time.monotonic() - started_at) * 1000),
            metadata=metadata,
        )

    def stream(self, request: ModelRequest):
        _raise_if_cancelled(request.cancellation_token, type(self).__name__)
        result = self.generate(request)
        if result.text:
            yield ModelEvent(kind="text_delta", text=result.text)
        for tool_call in result.tool_calls:
            yield ModelEvent(kind="tool_call", tool_call=tool_call)
        yield ModelEvent(kind="completed", result=result)


def _http_provider_error(provider, label, exc, body):
    status = int(exc.code)
    lowered = str(body).lower()
    if any(
        marker in lowered
        for marker in (
            "context length",
            "context window",
            "maximum context",
            "too many tokens",
            "reduce the length",
        )
    ):
        category, retryable, should_fallback = "context_overflow", False, False
        should_compress = True
    elif status == 429:
        category, retryable, should_fallback = "rate_limit", True, True
        should_compress = False
    elif status >= 500:
        category, retryable, should_fallback = "server", True, True
        should_compress = False
    elif status == 408:
        category, retryable, should_fallback = "timeout", True, True
        should_compress = False
    elif status == 402:
        category, retryable, should_fallback = "billing", False, True
        should_compress = False
    elif status == 404:
        category, retryable, should_fallback = "model_unavailable", False, True
        should_compress = False
    elif status in {401, 403}:
        category, retryable, should_fallback = "auth", False, False
        should_compress = False
    else:
        category, retryable, should_fallback = "request", False, False
        should_compress = False
    return ProviderError(
        f"{label} request failed with HTTP {status}: {body}",
        category=category,
        provider=provider,
        retryable=retryable,
        should_fallback=should_fallback,
        should_compress=should_compress,
        status_code=status,
    )


class FakeModelClient(_TypedModelClient):
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.prompts = []
        self.supports_prompt_cache = False
        self.last_completion_metadata = {}
        self.last_tool_calls = ()
        self.last_finish_reason = "stop"

    def complete(self, prompt, max_new_tokens, **kwargs):
        self.prompts.append(prompt)
        if not getattr(self, "last_completion_metadata", None):
            self.last_completion_metadata = {}
        if not self.outputs:
            raise RuntimeError("fake model ran out of outputs")
        return self.outputs.pop(0)


class OllamaModelClient(_TypedModelClient):
    def __init__(self, model, host, temperature, top_p, timeout):
        self.model = model
        self.host = host.rstrip("/")
        self.temperature = temperature
        self.top_p = top_p
        self.timeout = timeout
        self.supports_prompt_cache = False
        self.last_completion_metadata = {}
        self.last_tool_calls = ()
        self.last_finish_reason = "stop"

    def complete(self, prompt, max_new_tokens, timeout=None, **kwargs):
        # Ollama 当前不支持我们这里接入的 prompt cache 语义，
        # 所以 runtime 传下来的缓存参数会被忽略。
        self.last_completion_metadata = {}
        self.last_tool_calls = ()
        self.last_finish_reason = "stop"
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "raw": False,
            "think": False,
            "options": {
                "num_predict": max_new_tokens,
                "temperature": self.temperature,
                "top_p": self.top_p,
            },
        }
        request = urllib.request.Request(
            self.host + "/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=timeout or self.timeout
            ) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise _http_provider_error(
                type(self).__name__, "Ollama", exc, body
            ) from exc
        except urllib.error.URLError as exc:
            raise ProviderConnectionError(
                "Could not reach Ollama.\n"
                "Make sure `ollama serve` is running and the model is available.\n"
                f"Host: {self.host}\n"
                f"Model: {self.model}",
                provider=type(self).__name__,
            ) from exc

        if data.get("error"):
            raise ProviderProtocolError(
                f"Ollama error: {data['error']}", provider=type(self).__name__
            )
        self.last_completion_metadata = {
            "input_tokens": data.get("prompt_eval_count"),
            "output_tokens": data.get("eval_count"),
            "total_tokens": (
                int(data.get("prompt_eval_count") or 0)
                + int(data.get("eval_count") or 0)
            ),
            "usage_source": (
                "actual"
                if data.get("prompt_eval_count") is not None
                or data.get("eval_count") is not None
                else "missing"
            ),
        }
        return data.get("response", "")

    def stream(self, request: ModelRequest):
        started_at = time.monotonic()
        payload = {
            "model": self.model,
            "prompt": request.prompt,
            "stream": True,
            "raw": False,
            "think": False,
            "options": {
                "num_predict": request.max_output_tokens,
                "temperature": self.temperature,
                "top_p": self.top_p,
            },
        }
        http_request = urllib.request.Request(
            self.host + "/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        parts = []
        final_data = {}
        token = request.cancellation_token
        _raise_if_cancelled(token, type(self).__name__)
        try:
            with urllib.request.urlopen(
                http_request, timeout=request.timeout_seconds or self.timeout
            ) as response:
                remove_cancel = (
                    token.add_callback(response.close) if token is not None else None
                )
                try:
                    _raise_if_cancelled(token, type(self).__name__)
                    for raw_line in response:
                        _raise_if_cancelled(token, type(self).__name__)
                        line = raw_line.decode("utf-8").strip()
                        if not line:
                            continue
                        data = json.loads(line)
                        if data.get("error"):
                            raise ProviderProtocolError(
                                f"Ollama error: {data['error']}",
                                provider=type(self).__name__,
                            )
                        text = data.get("response") or ""
                        if text:
                            parts.append(text)
                            yield ModelEvent(kind="text_delta", text=text)
                        if data.get("done"):
                            final_data = data
                finally:
                    if remove_cancel is not None:
                        remove_cancel()
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise _http_provider_error(
                type(self).__name__, "Ollama", exc, body
            ) from exc
        except urllib.error.URLError as exc:
            _cancelled_during_io(token, type(self).__name__, exc)
            raise ProviderConnectionError(
                f"Could not reach Ollama at {self.host}",
                provider=type(self).__name__,
            ) from exc
        except json.JSONDecodeError as exc:
            raise ProviderProtocolError(
                "Ollama stream contained invalid JSON",
                provider=type(self).__name__,
            ) from exc
        except (OSError, ValueError) as exc:
            _cancelled_during_io(token, type(self).__name__, exc)
            raise
        _raise_if_cancelled(token, type(self).__name__)
        if not final_data:
            raise ProviderProtocolError(
                "Ollama stream ended without a done event",
                provider=type(self).__name__,
            )
        metadata = {
            "input_tokens": final_data.get("prompt_eval_count"),
            "output_tokens": final_data.get("eval_count"),
            "total_tokens": (
                int(final_data.get("prompt_eval_count") or 0)
                + int(final_data.get("eval_count") or 0)
            ),
            "usage_source": _usage_source(
                final_data, "prompt_eval_count", "eval_count"
            ),
        }
        result = ModelResult(
            text="".join(parts),
            usage=ModelUsage.from_metadata(metadata),
            provider=_provider_name(self),
            model=self.model,
            latency_ms=int((time.monotonic() - started_at) * 1000),
            metadata=metadata,
        )
        self.last_completion_metadata = metadata
        yield ModelEvent(kind="completed", result=result)


def _normalize_versioned_base_url(base_url):
    base = str(base_url).rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    return base


def _extract_openai_text(data):
    if data.get("output_text"):
        return data["output_text"]

    for item in data.get("output", []):
        for content in item.get("content", []):
            if isinstance(content, dict):
                text = content.get("text")
                if text:
                    return text

    choices = data.get("choices", [])
    if choices:
        message = choices[0].get("message", {})
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text")
                    if text:
                        return text

    return ""


def _extract_openai_reasoning(data):
    blocks = tuple(
        dict(item)
        for item in data.get("output", [])
        if isinstance(item, dict) and item.get("type") == "reasoning"
    )
    summary_parts = []
    for block in blocks:
        for part in block.get("summary") or []:
            if not isinstance(part, dict) or part.get("type") != "summary_text":
                continue
            text = part.get("text")
            if isinstance(text, str) and text:
                summary_parts.append(text)

    choices = data.get("choices") or []
    if not summary_parts and choices:
        message = choices[0].get("message") or {}
        reasoning = message.get("reasoning_content") or message.get("reasoning")
        if isinstance(reasoning, str) and reasoning:
            summary_parts.append(reasoning)
    return "\n".join(summary_parts).strip(), blocks


def _decode_tool_arguments(value, *, provider):
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        raise ProviderProtocolError(
            "native tool arguments must be a JSON object",
            provider=provider,
        )
    try:
        arguments = json.loads(value or "{}")
    except json.JSONDecodeError:
        try:
            arguments = json_repair.loads(value or "{}")
        except Exception as exc:
            raise ProviderProtocolError(
                "native tool arguments are not repairable JSON",
                provider=provider,
            ) from exc
    if not isinstance(arguments, dict):
        raise ProviderProtocolError(
            "native tool arguments must decode to an object",
            provider=provider,
        )
    return arguments


def _extract_openai_tool_calls(data, *, provider):
    calls = []
    for item in data.get("output", []):
        if not isinstance(item, dict) or item.get("type") != "function_call":
            continue
        calls.append(
            ToolCall(
                id=str(item.get("call_id") or item.get("id") or ""),
                name=str(item.get("name") or ""),
                arguments=_decode_tool_arguments(
                    item.get("arguments", "{}"), provider=provider
                ),
            )
        )
    choices = data.get("choices") or []
    if choices:
        message = choices[0].get("message") or {}
        for item in message.get("tool_calls") or []:
            function = item.get("function") or {}
            calls.append(
                ToolCall(
                    id=str(item.get("id") or ""),
                    name=str(function.get("name") or ""),
                    arguments=_decode_tool_arguments(
                        function.get("arguments", "{}"), provider=provider
                    ),
                )
            )
    return tuple(calls)


def _extract_openai_text_from_sse(body_text):
    last_response = None
    deltas = []
    for line in body_text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:") :].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            continue
        event_type = event.get("type", "")
        if event_type == "response.output_text.delta":
            delta = event.get("delta")
            if isinstance(delta, str):
                deltas.append(delta)
            continue
        if event_type == "response.output_text.done":
            text = event.get("text")
            if isinstance(text, str) and text:
                return text
        part = event.get("part")
        if isinstance(part, dict):
            text = part.get("text")
            if isinstance(text, str) and text:
                return text
        item = event.get("item")
        if isinstance(item, dict):
            text = _extract_openai_text({"output": [item]})
            if text:
                return text
        response = event.get("response")
        if isinstance(response, dict):
            last_response = response
            text = _extract_openai_text(response)
            if text:
                return text
        text = _extract_openai_text(event)
        if text:
            return text
    if deltas:
        return "".join(deltas)
    if isinstance(last_response, dict):
        return _extract_openai_text(last_response)
    return ""


def _extract_openai_response_from_sse(body_text):
    last_response = None
    deltas = []
    for line in body_text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:") :].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            continue
        response = event.get("response")
        if isinstance(response, dict):
            last_response = response
            if event.get("type") == "response.completed":
                text = _extract_openai_text(response)
                if text:
                    return text, response
        event_type = event.get("type", "")
        if event_type == "response.output_text.delta":
            delta = event.get("delta")
            if isinstance(delta, str):
                deltas.append(delta)
        elif event_type == "response.output_text.done":
            text = event.get("text")
            if isinstance(text, str) and text:
                return text, last_response or {}
        else:
            text = _extract_openai_text(event)
            if text:
                return text, event
    if deltas:
        return "".join(deltas), last_response or {}
    if isinstance(last_response, dict):
        return _extract_openai_text(last_response), last_response
    return "", {}


def _extract_usage_cache_details(data):
    # 把不同 OpenAI-compatible 返回里的 usage 字段整理成统一结构，
    # 让 runtime/trace/report 不需要关心 provider 细节。
    usage = data.get("usage") or {}
    input_tokens = usage.get("input_tokens", usage.get("prompt_tokens"))
    output_tokens = usage.get("output_tokens", usage.get("completion_tokens"))
    input_details = (
        usage.get("input_tokens_details") or usage.get("prompt_tokens_details") or {}
    )
    cached_tokens = int(input_details.get("cached_tokens") or 0)
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": usage.get("total_tokens"),
        "cached_tokens": cached_tokens,
        "cache_hit": cached_tokens > 0,
        "input_token_semantics": "total",
        "usage_source": _usage_source(
            usage,
            "input_tokens",
            "prompt_tokens",
            "output_tokens",
            "completion_tokens",
            "total_tokens",
        ),
    }


class OpenAICompatibleModelClient(_TypedModelClient):
    supports_structured_messages = True

    def __init__(self, model, base_url, api_key, temperature, timeout):
        self.model = model
        self.base_url = _normalize_versioned_base_url(base_url)
        self.api_key = api_key
        self.temperature = temperature
        self.timeout = timeout
        # 当前只在明确支持 prompt cache 语义的后端上启用这条链路，
        # 避免对不支持的后端传一个“看起来统一、其实没意义”的伪参数。
        self.supports_prompt_cache = any(
            host in self.base_url for host in ("openai.com", "right.codes")
        )
        self.last_completion_metadata = {}
        self.last_tool_calls = ()
        self.last_finish_reason = "stop"
        self.last_reasoning_content = ""
        self.last_thinking_blocks = ()

    def complete(
        self,
        prompt,
        max_new_tokens,
        prompt_cache_key=None,
        prompt_cache_retention=None,
        timeout=None,
        tools=(),
        cancellation_token=None,
    ):
        """向 OpenAI-compatible `/responses` 接口发起一次模型调用。

        为什么存在：
        runtime 不应该知道 HTTP 细节、SSE 细节、usage 字段长什么样，
        更不应该自己去判断 prompt cache 参数要不要带。这个函数把这些后端
        细节都包起来，对上层暴露统一的 `complete()` 行为。

        输入 / 输出：
        - 输入：完整 prompt、最大输出 token，以及可选的 prompt cache 参数
        - 输出：模型最终文本；同时把 usage / cached_tokens 等元数据写进
          `self.last_completion_metadata`

        在 agent 链路里的位置：
        它位于 `RepoAgent.ask()` 的模型调用阶段，是稳定前缀缓存复用链路真正
        落到 provider API 的地方。
        """
        self.last_completion_metadata = {}
        self.last_tool_calls = ()
        self.last_finish_reason = "stop"
        self.last_reasoning_content = ""
        self.last_thinking_blocks = ()
        _raise_if_cancelled(cancellation_token, type(self).__name__)
        payload = {
            "model": self.model,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": prompt,
                        }
                    ],
                }
            ],
            "max_output_tokens": max_new_tokens,
            "stream": False,
        }
        if self.supports_prompt_cache:
            payload["include"] = ["reasoning.encrypted_content"]
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": dict(tool.parameters),
                }
                for tool in tools
            ]
        # runtime 传入的是“稳定前缀”的签名，而不是整段 prompt 的签名。
        # 这样缓存复用针对的是稳定段，不会因为动态 history 每轮变化而失效。
        if self.supports_prompt_cache and prompt_cache_key:
            payload["prompt_cache_key"] = prompt_cache_key
        if self.supports_prompt_cache and prompt_cache_retention:
            payload["prompt_cache_retention"] = prompt_cache_retention

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": OPENAI_COMPATIBLE_USER_AGENT,
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        request = urllib.request.Request(
            self.base_url + "/responses",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        attempts = 3
        for attempt in range(attempts):
            try:
                with urllib.request.urlopen(
                    request, timeout=timeout or self.timeout
                ) as response:
                    body_text = response.read().decode("utf-8")
                    headers = getattr(response, "headers", {}) or {}
                    content_type = headers.get("Content-Type", "")
                break
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                if exc.code >= 500 and attempt < attempts - 1:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise _http_provider_error(
                    type(self).__name__, "OpenAI-compatible", exc, body
                ) from exc
            except (urllib.error.URLError, RemoteDisconnected) as exc:
                if attempt < attempts - 1:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise ProviderConnectionError(
                    "Could not reach the OpenAI-compatible backend.\n"
                    f"Base URL: {self.base_url}\n"
                    f"Model: {self.model}",
                    provider=type(self).__name__,
                ) from exc
            except TimeoutError as exc:
                if attempt < attempts - 1:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise ProviderTimeoutError(
                    "OpenAI-compatible request timed out",
                    provider=type(self).__name__,
                ) from exc

        _raise_if_cancelled(cancellation_token, type(self).__name__)

        # 有些兼容后端返回普通 JSON，有些返回 SSE。
        # 这里两种都接住，并尽量统一抽取文本和 usage/cache 元数据。
        if content_type.startswith(
            "text/event-stream"
        ) or body_text.lstrip().startswith("data:"):
            text, response_data = _extract_openai_response_from_sse(body_text)
            if isinstance(response_data, dict) and response_data:
                # 这些元数据会一路传回 runtime，进入 trace 和 report，
                # 用来观察 prompt cache 是否真的命中。
                self.last_completion_metadata = {
                    "prompt_cache_supported": self.supports_prompt_cache,
                    "prompt_cache_key": prompt_cache_key,
                    "prompt_cache_retention": prompt_cache_retention,
                    **_extract_usage_cache_details(response_data),
                }
                self.last_tool_calls = _extract_openai_tool_calls(
                    response_data, provider=type(self).__name__
                )
                (
                    self.last_reasoning_content,
                    self.last_thinking_blocks,
                ) = _extract_openai_reasoning(response_data)
                self.last_finish_reason = (
                    "tool_calls" if self.last_tool_calls else "stop"
                )
            if text or self.last_tool_calls:
                return text
            raise ProviderProtocolError(
                "OpenAI-compatible error: could not extract text from event stream response",
                provider=type(self).__name__,
            )

        try:
            data = json.loads(body_text)
        except json.JSONDecodeError as exc:
            raise ProviderProtocolError(
                "OpenAI-compatible error: backend returned non-JSON content that could not be parsed",
                provider=type(self).__name__,
            ) from exc
        if data.get("error"):
            raise ProviderProtocolError(
                f"OpenAI-compatible error: {data['error']}",
                provider=type(self).__name__,
            )
        self.last_completion_metadata = {
            "prompt_cache_supported": self.supports_prompt_cache,
            "prompt_cache_key": prompt_cache_key,
            "prompt_cache_retention": prompt_cache_retention,
            **_extract_usage_cache_details(data),
        }
        self.last_tool_calls = _extract_openai_tool_calls(
            data, provider=type(self).__name__
        )
        (
            self.last_reasoning_content,
            self.last_thinking_blocks,
        ) = _extract_openai_reasoning(data)
        choices = data.get("choices") or []
        self.last_finish_reason = str(
            (choices[0].get("finish_reason") if choices else None)
            or ("tool_calls" if self.last_tool_calls else "stop")
        )
        return _extract_openai_text(data)

    def stream(self, request: ModelRequest):
        started_at = time.monotonic()
        payload = {
            "model": self.model,
            "input": _openai_response_input(request),
            "max_output_tokens": request.max_output_tokens,
            "stream": True,
        }
        if self.supports_prompt_cache:
            payload["include"] = ["reasoning.encrypted_content"]
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        if self.supports_prompt_cache and request.prompt_cache_key:
            payload["prompt_cache_key"] = request.prompt_cache_key
        if self.supports_prompt_cache and request.prompt_cache_retention:
            payload["prompt_cache_retention"] = request.prompt_cache_retention
        if request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": dict(tool.parameters),
                }
                for tool in request.tools
            ]
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "User-Agent": OPENAI_COMPATIBLE_USER_AGENT,
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        http_request = urllib.request.Request(
            self.base_url + "/responses",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        parts = []
        reasoning_parts = []
        final_data = None
        token = request.cancellation_token
        _raise_if_cancelled(token, type(self).__name__)
        try:
            with urllib.request.urlopen(
                http_request, timeout=request.timeout_seconds or self.timeout
            ) as response:
                remove_cancel = (
                    token.add_callback(response.close) if token is not None else None
                )
                try:
                    _raise_if_cancelled(token, type(self).__name__)
                    content_type = (getattr(response, "headers", {}) or {}).get(
                        "Content-Type", ""
                    )
                    if not content_type.startswith("text/event-stream"):
                        data = json.loads(response.read().decode("utf-8"))
                        _raise_if_cancelled(token, type(self).__name__)
                        yield from self._events_from_openai_response(
                            data, request, started_at
                        )
                        return
                    for raw_line in response:
                        _raise_if_cancelled(token, type(self).__name__)
                        line = raw_line.decode("utf-8").strip()
                        if not line.startswith("data:"):
                            continue
                        encoded = line[len("data:") :].strip()
                        if not encoded or encoded == "[DONE]":
                            continue
                        event = json.loads(encoded)
                        event_type = event.get("type")
                        if event_type == "response.output_text.delta":
                            text = event.get("delta") or ""
                            if text:
                                parts.append(text)
                                yield ModelEvent(kind="text_delta", text=text)
                        elif event_type == "response.reasoning_summary_text.delta":
                            reasoning = event.get("delta") or ""
                            if reasoning:
                                reasoning_parts.append(reasoning)
                        elif event_type == "response.completed":
                            final_data = event.get("response") or {}
                finally:
                    if remove_cancel is not None:
                        remove_cancel()
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise _http_provider_error(
                type(self).__name__, "OpenAI-compatible", exc, body
            ) from exc
        except (urllib.error.URLError, RemoteDisconnected) as exc:
            _cancelled_during_io(token, type(self).__name__, exc)
            raise ProviderConnectionError(
                f"Could not reach the OpenAI-compatible backend at {self.base_url}",
                provider=type(self).__name__,
            ) from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderProtocolError(
                "OpenAI-compatible stream contained invalid JSON",
                provider=type(self).__name__,
            ) from exc
        except (OSError, ValueError) as exc:
            _cancelled_during_io(token, type(self).__name__, exc)
            raise

        _raise_if_cancelled(token, type(self).__name__)

        if final_data is None:
            raise ProviderProtocolError(
                "OpenAI-compatible stream ended without response.completed",
                provider=type(self).__name__,
            )
        if final_data is not None:
            tool_calls = _extract_openai_tool_calls(
                final_data, provider=type(self).__name__
            )
            usage_payload = final_data
            final_text = _extract_openai_text(final_data) or "".join(parts)
            reasoning_content, thinking_blocks = _extract_openai_reasoning(final_data)
            reasoning_content = reasoning_content or "".join(reasoning_parts)
        metadata = {
            "prompt_cache_supported": self.supports_prompt_cache,
            "prompt_cache_key": request.prompt_cache_key,
            "prompt_cache_retention": request.prompt_cache_retention,
            **_extract_usage_cache_details(usage_payload),
        }
        result = ModelResult(
            text=final_text,
            tool_calls=tool_calls,
            reasoning_content=reasoning_content,
            thinking_blocks=thinking_blocks,
            finish_reason="tool_calls" if tool_calls else "stop",
            usage=ModelUsage.from_metadata(metadata),
            provider=_provider_name(self),
            model=self.model,
            latency_ms=int((time.monotonic() - started_at) * 1000),
            metadata=metadata,
        )
        self.last_completion_metadata = metadata
        self.last_tool_calls = tool_calls
        self.last_finish_reason = result.finish_reason
        for tool_call in tool_calls:
            yield ModelEvent(kind="tool_call", tool_call=tool_call)
        yield ModelEvent(kind="completed", result=result)

    def _events_from_openai_response(self, data, request, started_at):
        if data.get("error"):
            raise ProviderProtocolError(
                f"OpenAI-compatible error: {data['error']}",
                provider=type(self).__name__,
            )
        text = _extract_openai_text(data)
        tool_calls = _extract_openai_tool_calls(data, provider=type(self).__name__)
        reasoning_content, thinking_blocks = _extract_openai_reasoning(data)
        metadata = {
            "prompt_cache_supported": self.supports_prompt_cache,
            "prompt_cache_key": request.prompt_cache_key,
            "prompt_cache_retention": request.prompt_cache_retention,
            **_extract_usage_cache_details(data),
        }
        if text:
            yield ModelEvent(kind="text_delta", text=text)
        for tool_call in tool_calls:
            yield ModelEvent(kind="tool_call", tool_call=tool_call)
        result = ModelResult(
            text=text,
            tool_calls=tool_calls,
            reasoning_content=reasoning_content,
            thinking_blocks=thinking_blocks,
            finish_reason="tool_calls" if tool_calls else "stop",
            usage=ModelUsage.from_metadata(metadata),
            provider=_provider_name(self),
            model=self.model,
            latency_ms=int((time.monotonic() - started_at) * 1000),
            metadata=metadata,
        )
        self.last_completion_metadata = metadata
        self.last_tool_calls = tool_calls
        self.last_finish_reason = result.finish_reason
        yield ModelEvent(kind="completed", result=result)


def _extract_anthropic_text(data):
    for item in data.get("content", []):
        if isinstance(item, dict) and item.get("type") == "text":
            text = item.get("text")
            if isinstance(text, str) and text:
                return text
    return ""


def _extract_anthropic_thinking(data):
    blocks = tuple(
        dict(item)
        for item in data.get("content", [])
        if isinstance(item, dict) and item.get("type") == "thinking"
    )
    reasoning = "\n".join(str(block.get("thinking") or "") for block in blocks).strip()
    return reasoning, blocks


def _extract_anthropic_tool_calls(data, *, provider):
    return tuple(
        ToolCall(
            id=str(item.get("id") or ""),
            name=str(item.get("name") or ""),
            arguments=_decode_tool_arguments(
                item.get("input") or {}, provider=provider
            ),
        )
        for item in data.get("content", [])
        if isinstance(item, dict) and item.get("type") == "tool_use"
    )


class AnthropicCompatibleModelClient(_TypedModelClient):
    supports_structured_messages = True

    def __init__(self, model, base_url, api_key, temperature, timeout):
        self.model = model
        self.base_url = _normalize_versioned_base_url(base_url)
        self.api_key = api_key
        self.temperature = temperature
        self.timeout = timeout
        self.supports_prompt_cache = False
        self.last_completion_metadata = {}
        self.last_tool_calls = ()
        self.last_finish_reason = "stop"

    def complete(
        self,
        prompt,
        max_new_tokens,
        prompt_cache_key=None,
        prompt_cache_retention=None,
        timeout=None,
        tools=(),
        cancellation_token=None,
    ):
        # 为了保持统一接口，runtime 仍然会传缓存参数进来；
        # 这里只是显式丢弃，因为当前 Anthropic-compatible 路径没有接缓存复用。
        del prompt_cache_key, prompt_cache_retention
        self.last_completion_metadata = {}
        self.last_tool_calls = ()
        self.last_finish_reason = "stop"
        _raise_if_cancelled(cancellation_token, type(self).__name__)
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": prompt,
                        }
                    ],
                }
            ],
            "max_tokens": max_new_tokens,
            "stream": False,
        }
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        if tools:
            payload["tools"] = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": dict(tool.parameters),
                }
                for tool in tools
            ]

        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }

        request = urllib.request.Request(
            self.base_url + "/messages",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        attempts = 3
        for attempt in range(attempts):
            try:
                with urllib.request.urlopen(
                    request, timeout=timeout or self.timeout
                ) as response:
                    body_text = response.read().decode("utf-8")
                break
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                if exc.code >= 500 and attempt < attempts - 1:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise _http_provider_error(
                    type(self).__name__, "Anthropic-compatible", exc, body
                ) from exc
            except (urllib.error.URLError, RemoteDisconnected) as exc:
                if attempt < attempts - 1:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise ProviderConnectionError(
                    "Could not reach the Anthropic-compatible backend.\n"
                    f"Base URL: {self.base_url}\n"
                    f"Model: {self.model}",
                    provider=type(self).__name__,
                ) from exc
            except TimeoutError as exc:
                if attempt < attempts - 1:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise ProviderTimeoutError(
                    "Anthropic-compatible request timed out",
                    provider=type(self).__name__,
                ) from exc

        _raise_if_cancelled(cancellation_token, type(self).__name__)

        try:
            data = json.loads(body_text)
        except json.JSONDecodeError as exc:
            raise ProviderProtocolError(
                "Anthropic-compatible error: backend returned non-JSON content that could not be parsed",
                provider=type(self).__name__,
            ) from exc
        if data.get("error"):
            raise ProviderProtocolError(
                f"Anthropic-compatible error: {data['error']}",
                provider=type(self).__name__,
            )
        usage = data.get("usage") or {}
        self.last_completion_metadata = {
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "total_tokens": (
                int(usage.get("input_tokens") or 0)
                + int(usage.get("output_tokens") or 0)
            ),
            "cache_read_tokens": usage.get("cache_read_input_tokens", 0),
            "cache_write_tokens": usage.get("cache_creation_input_tokens", 0),
            "input_token_semantics": "fresh",
            "usage_source": _usage_source(
                usage,
                "input_tokens",
                "output_tokens",
                "cache_read_input_tokens",
                "cache_creation_input_tokens",
            ),
        }
        self.last_tool_calls = _extract_anthropic_tool_calls(
            data, provider=type(self).__name__
        )
        self.last_finish_reason = str(
            data.get("stop_reason")
            or ("tool_calls" if self.last_tool_calls else "stop")
        )
        text = _extract_anthropic_text(data)
        if text or self.last_tool_calls:
            return text
        raise ProviderProtocolError(
            "Anthropic-compatible error: could not extract text from response",
            provider=_provider_name(self),
        )

    def stream(self, request: ModelRequest):
        started_at = time.monotonic()
        payload = {
            "model": self.model,
            "messages": _anthropic_messages(request),
            "max_tokens": request.max_output_tokens,
            "stream": True,
        }
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        if request.tools:
            payload["tools"] = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": dict(tool.parameters),
                }
                for tool in request.tools
            ]
        http_request = urllib.request.Request(
            self.base_url + "/messages",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        parts = []
        blocks = {}
        thinking_parts = []
        thinking_blocks = {}
        usage = {}
        finish_reason = "stop"
        saw_stop = False
        token = request.cancellation_token
        _raise_if_cancelled(token, type(self).__name__)
        try:
            with urllib.request.urlopen(
                http_request, timeout=request.timeout_seconds or self.timeout
            ) as response:
                remove_cancel = (
                    token.add_callback(response.close) if token is not None else None
                )
                try:
                    _raise_if_cancelled(token, type(self).__name__)
                    content_type = (getattr(response, "headers", {}) or {}).get(
                        "Content-Type", ""
                    )
                    if not content_type.startswith("text/event-stream"):
                        data = json.loads(response.read().decode("utf-8"))
                        _raise_if_cancelled(token, type(self).__name__)
                        yield from self._events_from_anthropic_response(
                            data, started_at
                        )
                        return
                    for raw_line in response:
                        _raise_if_cancelled(token, type(self).__name__)
                        line = raw_line.decode("utf-8").strip()
                        if not line.startswith("data:"):
                            continue
                        encoded = line[len("data:") :].strip()
                        if not encoded:
                            continue
                        event = json.loads(encoded)
                        event_type = event.get("type")
                        if event_type == "message_start":
                            usage.update(
                                (event.get("message") or {}).get("usage") or {}
                            )
                        elif event_type == "content_block_start":
                            block = event.get("content_block") or {}
                            index = int(event.get("index") or 0)
                            if block.get("type") == "tool_use":
                                blocks[index] = {
                                    "id": block.get("id"),
                                    "name": block.get("name"),
                                    "arguments": "",
                                }
                            elif block.get("type") == "thinking":
                                thinking = str(block.get("thinking") or "")
                                thinking_blocks[index] = dict(block)
                                if thinking:
                                    thinking_parts.append(thinking)
                            elif block.get("type") == "text" and block.get("text"):
                                text = str(block["text"])
                                parts.append(text)
                                yield ModelEvent(kind="text_delta", text=text)
                        elif event_type == "content_block_delta":
                            delta = event.get("delta") or {}
                            index = int(event.get("index") or 0)
                            if delta.get("type") == "text_delta":
                                text = str(delta.get("text") or "")
                                if text:
                                    parts.append(text)
                                    yield ModelEvent(kind="text_delta", text=text)
                            elif delta.get("type") == "thinking_delta":
                                thinking = str(delta.get("thinking") or "")
                                if thinking:
                                    thinking_parts.append(thinking)
                                    block = thinking_blocks.setdefault(
                                        index, {"type": "thinking", "thinking": ""}
                                    )
                                    block["thinking"] = (
                                        str(block.get("thinking") or "") + thinking
                                    )
                            elif delta.get("type") == "signature_delta":
                                block = thinking_blocks.setdefault(
                                    index, {"type": "thinking", "thinking": ""}
                                )
                                block["signature"] = str(delta.get("signature") or "")
                            elif delta.get("type") == "input_json_delta":
                                block = blocks.setdefault(
                                    index,
                                    {"id": "", "name": "", "arguments": ""},
                                )
                                block["arguments"] += delta.get("partial_json") or ""
                        elif event_type == "message_delta":
                            delta = event.get("delta") or {}
                            finish_reason = str(
                                delta.get("stop_reason") or finish_reason
                            )
                            usage.update(event.get("usage") or {})
                        elif event_type == "message_stop":
                            saw_stop = True
                        elif event_type == "error":
                            error = event.get("error") or {}
                            raise ProviderProtocolError(
                                "Anthropic-compatible stream error: "
                                + str(error.get("type", "unknown")),
                                provider=type(self).__name__,
                            )
                finally:
                    if remove_cancel is not None:
                        remove_cancel()
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise _http_provider_error(
                type(self).__name__, "Anthropic-compatible", exc, body
            ) from exc
        except (urllib.error.URLError, RemoteDisconnected) as exc:
            _cancelled_during_io(token, type(self).__name__, exc)
            raise ProviderConnectionError(
                f"Could not reach the Anthropic-compatible backend at {self.base_url}",
                provider=type(self).__name__,
            ) from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderProtocolError(
                "Anthropic-compatible stream contained invalid JSON",
                provider=type(self).__name__,
            ) from exc
        except (OSError, ValueError) as exc:
            _cancelled_during_io(token, type(self).__name__, exc)
            raise
        _raise_if_cancelled(token, type(self).__name__)
        if not saw_stop:
            raise ProviderProtocolError(
                "Anthropic-compatible stream ended without message_stop",
                provider=type(self).__name__,
            )
        tool_calls = tuple(
            ToolCall(
                id=str(block.get("id") or ""),
                name=str(block.get("name") or ""),
                arguments=_decode_tool_arguments(
                    block.get("arguments") or "{}",
                    provider=type(self).__name__,
                ),
            )
            for block in blocks.values()
        )
        metadata = {
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "total_tokens": (
                int(usage.get("input_tokens") or 0)
                + int(usage.get("output_tokens") or 0)
            ),
            "cache_read_tokens": usage.get("cache_read_input_tokens", 0),
            "cache_write_tokens": usage.get("cache_creation_input_tokens", 0),
            "input_token_semantics": "fresh",
            "usage_source": _usage_source(
                usage,
                "input_tokens",
                "output_tokens",
                "cache_read_input_tokens",
                "cache_creation_input_tokens",
            ),
        }
        result = ModelResult(
            text="".join(parts),
            tool_calls=tool_calls,
            reasoning_content="".join(thinking_parts),
            thinking_blocks=tuple(thinking_blocks.values()),
            finish_reason=finish_reason,
            usage=ModelUsage.from_metadata(metadata),
            provider=_provider_name(self),
            model=self.model,
            latency_ms=int((time.monotonic() - started_at) * 1000),
            metadata=metadata,
        )
        self.last_completion_metadata = metadata
        self.last_tool_calls = tool_calls
        self.last_finish_reason = finish_reason
        for tool_call in tool_calls:
            yield ModelEvent(kind="tool_call", tool_call=tool_call)
        yield ModelEvent(kind="completed", result=result)

    def _events_from_anthropic_response(self, data, started_at):
        if data.get("error"):
            raise ProviderProtocolError(
                f"Anthropic-compatible error: {data['error']}",
                provider=type(self).__name__,
            )
        text = _extract_anthropic_text(data)
        reasoning_content, thinking_blocks = _extract_anthropic_thinking(data)
        tool_calls = _extract_anthropic_tool_calls(data, provider=type(self).__name__)
        usage = data.get("usage") or {}
        metadata = {
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "total_tokens": (
                int(usage.get("input_tokens") or 0)
                + int(usage.get("output_tokens") or 0)
            ),
            "cache_read_tokens": usage.get("cache_read_input_tokens", 0),
            "cache_write_tokens": usage.get("cache_creation_input_tokens", 0),
            "input_token_semantics": "fresh",
            "usage_source": _usage_source(
                usage,
                "input_tokens",
                "output_tokens",
                "cache_read_input_tokens",
                "cache_creation_input_tokens",
            ),
        }
        if text:
            yield ModelEvent(kind="text_delta", text=text)
        for tool_call in tool_calls:
            yield ModelEvent(kind="tool_call", tool_call=tool_call)
        finish_reason = str(
            data.get("stop_reason") or ("tool_calls" if tool_calls else "stop")
        )
        result = ModelResult(
            text=text,
            tool_calls=tool_calls,
            reasoning_content=reasoning_content,
            thinking_blocks=thinking_blocks,
            finish_reason=finish_reason,
            usage=ModelUsage.from_metadata(metadata),
            provider=_provider_name(self),
            model=self.model,
            latency_ms=int((time.monotonic() - started_at) * 1000),
            metadata=metadata,
        )
        self.last_completion_metadata = metadata
        self.last_tool_calls = tool_calls
        self.last_finish_reason = finish_reason
        yield ModelEvent(kind="completed", result=result)
