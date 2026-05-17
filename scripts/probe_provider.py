#!/usr/bin/env python3
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


DEFAULT_TIMEOUT = 30
DEFAULT_RESPONSE_PROMPT = "Reply with exactly OK."


def _normalize_versioned_base_url(base_url):
    base = str(base_url).rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    return base


def _mask_secret(value):
    text = str(value or "")
    if len(text) <= 8:
        return "*" * len(text)
    return text[:4] + "*" * (len(text) - 8) + text[-4:]


def _http_json(method, url, payload=None, api_key="", timeout=DEFAULT_TIMEOUT, extra_headers=None):
    headers = {"Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    if api_key:
        headers.setdefault("Authorization", f"Bearer {api_key}")
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body_text = response.read().decode("utf-8", errors="replace")
            return {
                "ok": True,
                "status": int(getattr(response, "status", 200)),
                "body_text": body_text,
                "json": _safe_json_loads(body_text),
            }
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")
        return {
            "ok": False,
            "status": int(exc.code),
            "body_text": body_text,
            "json": _safe_json_loads(body_text),
            "error": f"HTTP {exc.code}",
        }
    except urllib.error.URLError as exc:
        return {
            "ok": False,
            "status": 0,
            "body_text": "",
            "json": None,
            "error": f"Network error: {exc}",
        }


def _safe_json_loads(text):
    try:
        return json.loads(text)
    except Exception:
        return None


def _extract_openai_text(data):
    if not isinstance(data, dict):
        return ""
    if data.get("output_text"):
        return str(data["output_text"])
    for item in data.get("output", []):
        for content in item.get("content", []):
            if isinstance(content, dict) and content.get("text"):
                return str(content["text"])
    choices = data.get("choices", [])
    if choices:
        message = choices[0].get("message", {})
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("text"):
                    return str(item["text"])
    return ""


def _extract_anthropic_text(data):
    if not isinstance(data, dict):
        return ""
    for item in data.get("content", []):
        if isinstance(item, dict) and item.get("type") == "text" and item.get("text"):
            return str(item["text"])
    return ""


def _extract_model_ids(data):
    if isinstance(data, dict) and isinstance(data.get("data"), list):
        return [
            str(item.get("id", "")).strip()
            for item in data["data"]
            if isinstance(item, dict) and str(item.get("id", "")).strip()
        ]
    if isinstance(data, list):
        return [
            str(item.get("id", "")).strip()
            for item in data
            if isinstance(item, dict) and str(item.get("id", "")).strip()
        ]
    return []


def _pick_model(explicit_model, model_ids):
    if str(explicit_model or "").strip():
        return str(explicit_model).strip()
    if model_ids:
        return model_ids[0]
    env_candidates = (
        os.environ.get("OPENAI_MODEL", ""),
        os.environ.get("ANTHROPIC_MODEL", ""),
        "gpt-5.4",
    )
    for candidate in env_candidates:
        candidate = str(candidate or "").strip()
        if candidate:
            return candidate
    return ""


def _probe_openai_models(base_url, api_key, timeout):
    return _http_json("GET", base_url + "/models", api_key=api_key, timeout=timeout)


def _probe_openai_responses(base_url, api_key, timeout, model, prompt):
    payload = {
        "model": model,
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
        "max_output_tokens": 32,
        "stream": False,
    }
    result = _http_json("POST", base_url + "/responses", payload=payload, api_key=api_key, timeout=timeout)
    result["text"] = _extract_openai_text(result.get("json"))
    return result


def _probe_openai_chat(base_url, api_key, timeout, model, prompt):
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 32,
        "stream": False,
    }
    result = _http_json("POST", base_url + "/chat/completions", payload=payload, api_key=api_key, timeout=timeout)
    result["text"] = _extract_openai_text(result.get("json"))
    return result


def _probe_anthropic_messages(base_url, api_key, timeout, model, prompt):
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": prompt}],
            }
        ],
        "max_tokens": 32,
        "stream": False,
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }
    result = _http_json(
        "POST",
        base_url + "/messages",
        payload=payload,
        api_key="",
        timeout=timeout,
        extra_headers=headers,
    )
    result["text"] = _extract_anthropic_text(result.get("json"))
    return result


def _summarize_endpoint(label, result):
    status = result.get("status", 0)
    ok = bool(result.get("ok"))
    text = str(result.get("text", "")).strip()
    error = str(result.get("error", "")).strip()
    return {
        "label": label,
        "ok": ok,
        "status": status,
        "text": text,
        "error": error,
    }


def build_arg_parser():
    parser = argparse.ArgumentParser(
        description="Probe an unknown model backend and infer whether pico should use openai or anthropic provider mode."
    )
    parser.add_argument("--provider-label", default="unknown", help="Human label only. Not used for routing.")
    parser.add_argument("--base-url", default=os.environ.get("OPENAI_API_BASE", ""), help="Provider base URL.")
    parser.add_argument("--api-key", default=os.environ.get("OPENAI_API_KEY", ""), help="API key for the provider.")
    parser.add_argument("--model", default="", help="Optional model override. If omitted, the script tries /models first.")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="HTTP timeout in seconds.")
    parser.add_argument("--prompt", default=DEFAULT_RESPONSE_PROMPT, help="Prompt used for compatibility probes.")
    parser.add_argument("--output-json", default="", help="Optional JSON artifact output path.")
    return parser


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    base_url = str(args.base_url or "").strip()
    api_key = str(args.api_key or "").strip()
    if not base_url:
        print("error: missing --base-url or OPENAI_API_BASE", file=sys.stderr)
        return 2
    if not api_key:
        print("error: missing --api-key or OPENAI_API_KEY", file=sys.stderr)
        return 2

    versioned_base_url = _normalize_versioned_base_url(base_url)
    models_result = _probe_openai_models(versioned_base_url, api_key, args.timeout)
    model_ids = _extract_model_ids(models_result.get("json"))
    selected_model = _pick_model(args.model, model_ids)

    responses_result = _probe_openai_responses(
        versioned_base_url,
        api_key,
        args.timeout,
        selected_model,
        args.prompt,
    )
    chat_result = _probe_openai_chat(
        versioned_base_url,
        api_key,
        args.timeout,
        selected_model,
        args.prompt,
    )
    anthropic_result = _probe_anthropic_messages(
        versioned_base_url,
        api_key,
        args.timeout,
        selected_model,
        args.prompt,
    )

    openai_compatible = bool(responses_result.get("ok") or chat_result.get("ok"))
    anthropic_compatible = bool(anthropic_result.get("ok"))
    recommended_provider = "openai" if openai_compatible else ("anthropic" if anthropic_compatible else "unknown")

    report = {
        "provider_label": args.provider_label,
        "base_url_input": base_url,
        "base_url_normalized": versioned_base_url,
        "api_key_masked": _mask_secret(api_key),
        "selected_model": selected_model,
        "discovered_models": model_ids[:20],
        "compatibility": {
            "openai_compatible": openai_compatible,
            "anthropic_compatible": anthropic_compatible,
            "recommended_pico_provider": recommended_provider,
        },
        "endpoints": {
            "get_models": _summarize_endpoint("GET /v1/models", models_result),
            "post_responses": _summarize_endpoint("POST /v1/responses", responses_result),
            "post_chat_completions": _summarize_endpoint("POST /v1/chat/completions", chat_result),
            "post_messages": _summarize_endpoint("POST /v1/messages", anthropic_result),
        },
    }

    if args.output_json:
        output = Path(args.output_json)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True))
    return 0 if recommended_provider != "unknown" else 1


if __name__ == "__main__":
    raise SystemExit(main())
