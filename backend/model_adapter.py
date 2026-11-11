import json
import os
import time
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional, Type
from pydantic import BaseModel

try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

try:
    from google import genai
    from google.genai import types
    HAS_GOOGLE_GENAI_SDK = True
except ImportError:
    genai = None  # type: ignore[assignment]
    types = None  # type: ignore[assignment]
    HAS_GOOGLE_GENAI_SDK = False


SUPPORTED_MODELS: List[Dict[str, str]] = [
    {
        "id": "gemini-2.5-flash",
        "label": "Google Gemini 2.5 Flash (Default — Fast)",
        "provider": "google",
        "env_key": "GEMINI_API_KEY",
        "api_model": "gemini-2.5-flash",
    },
    {
        "id": "gemini-2.5-pro",
        "label": "Google Gemini 2.5 Pro (Deep Compliance Reasoning)",
        "provider": "google",
        "env_key": "GEMINI_API_KEY",
        "api_model": "gemini-2.5-pro",
    },
    {
        "id": "claude-sonnet",
        "label": "Anthropic Claude Sonnet 4",
        "provider": "anthropic",
        "env_key": "ANTHROPIC_API_KEY",
        "api_model": "claude-sonnet-4-20250514",
    },
    {
        "id": "gpt-4.1",
        "label": "OpenAI GPT-4.1",
        "provider": "openai",
        "env_key": "OPENAI_API_KEY",
        "api_model": "gpt-4.1",
    },
    {
        "id": "o3-mini",
        "label": "OpenAI o3-mini",
        "provider": "openai",
        "env_key": "OPENAI_API_KEY",
        "api_model": "o3-mini",
    },
]

DEFAULT_FALLBACK_ORDER: List[str] = [
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gpt-4.1",
    "claude-sonnet",
    "o3-mini",
]

MODEL_HEALTH_TTL_SECONDS = int(os.environ.get("MODEL_HEALTH_TTL_SECONDS", "60"))
_model_health_cache: Optional[Dict[str, Any]] = None
_model_health_timestamp: float = 0.0
_last_routing_telemetry: Dict[str, Any] = {
    "requested_model": None,
    "executed_model": None,
    "failover_triggered": False,
    "attempted_models": [],
}


def _is_key_configured(env_key: str) -> bool:
    val = (os.environ.get(env_key) or "").strip()
    if not val:
        return False
    placeholder_prefixes = ("your_", "optional_", "replace_", "insert_")
    return not val.lower().startswith(placeholder_prefixes)


def list_available_models() -> List[Dict[str, Any]]:
    models_info = []
    for m in SUPPORTED_MODELS:
        configured = _is_key_configured(m["env_key"])
        models_info.append({
            "id": m["id"],
            "label": m["label"],
            "provider": m["provider"],
            "available": configured,
        })
    return models_info


def get_model_meta(model_id: Optional[str]) -> Dict[str, str]:
    if not model_id:
        return SUPPORTED_MODELS[0]
    for m in SUPPORTED_MODELS:
        if m["id"] == model_id:
            return m
    return SUPPORTED_MODELS[0]


def build_model_failover_chain(requested_model_id: Optional[str] = "gemini-2.5-flash") -> List[str]:
    """
    Builds a deduplicated ordered list of callable model IDs starting with requested_model_id
    (if its provider API key is configured), followed by configured fallback models.
    Ensures at least a 2-model chain is present even in mock/test environments.
    """
    env_fallbacks_raw = os.environ.get("GATEKEEPER_MODEL_FALLBACKS", "").strip()
    if env_fallbacks_raw:
        custom_fallbacks = [m.strip() for m in env_fallbacks_raw.split(",") if m.strip()]
    else:
        custom_fallbacks = list(DEFAULT_FALLBACK_ORDER)

    candidates = []
    if requested_model_id:
        candidates.append(requested_model_id)
    candidates.extend(custom_fallbacks)

    configured_chain: List[str] = []
    seen = set()
    for cid in candidates:
        meta = get_model_meta(cid)
        resolved_id = meta["id"]
        if resolved_id in seen:
            continue
        seen.add(resolved_id)
        if _is_key_configured(meta["env_key"]):
            configured_chain.append(resolved_id)

    if not configured_chain:
        primary_id = get_model_meta(requested_model_id)["id"]
        fallback_id = "gemini-2.5-pro" if primary_id == "gemini-2.5-flash" else "gemini-2.5-flash"
        return [primary_id, fallback_id]

    return configured_chain


def is_retriable_model_error(error: Exception) -> bool:
    """
    Determines whether an exception from a model provider should trigger automatic failover
    to the next model in the chain (HTTP 404, 408, 410, 429, 5xx, timeouts, or connection errors).
    """
    if isinstance(error, urllib.error.HTTPError):
        if error.code in (404, 408, 410, 429) or error.code >= 500:
            return True
    if isinstance(error, (urllib.error.URLError, TimeoutError, ConnectionError)):
        return True

    status = getattr(error, "status", None) or getattr(error, "status_code", None) or getattr(error, "code", None)
    if isinstance(status, int) and (status in (404, 408, 410, 429) or status >= 500):
        return True

    msg = str(error).lower()
    retriable_tokens = (
        "429",
        "rate limit",
        "quota",
        "timeout",
        "timed out",
        "500",
        "502",
        "503",
        "504",
        "overloaded",
        "unavailable",
        "model_not_found",
        "deprecated",
        "not found",
        "connection",
    )
    return any(tok in msg for tok in retriable_tokens)


def get_last_routing_telemetry() -> Dict[str, Any]:
    return dict(_last_routing_telemetry)


def probe_model_chain_health(force: bool = False) -> Dict[str, Any]:
    """
    Returns a cached health and readiness snapshot of the multi-provider model failover chains.
    """
    global _model_health_cache, _model_health_timestamp
    now = time.time()
    if (
        not force
        and _model_health_cache is not None
        and (now - _model_health_timestamp) < MODEL_HEALTH_TTL_SECONDS
    ):
        return _model_health_cache

    models_status = list_available_models()
    drafting_chain = build_model_failover_chain("gemini-2.5-flash")
    evaluation_chain = build_model_failover_chain("gemini-2.5-pro")
    configured_providers = sorted({m["provider"] for m in models_status if m["available"]})

    snapshot: Dict[str, Any] = {
        "status": "ready" if configured_providers else "unconfigured",
        "configured_providers": configured_providers,
        "drafting_chain": {
            "active": drafting_chain[0] if drafting_chain else None,
            "fallbacks_available": drafting_chain[1:],
        },
        "evaluation_chain": {
            "active": evaluation_chain[0] if evaluation_chain else None,
            "fallbacks_available": evaluation_chain[1:],
        },
        "models": models_status,
        "last_routing": get_last_routing_telemetry(),
        "checked_at_epoch": int(now),
    }
    _model_health_cache = snapshot
    _model_health_timestamp = now
    return snapshot


def _invoke_single_model_text(
    meta: Dict[str, str],
    system_instruction: str,
    user_prompt: str,
    temperature: float,
) -> str:
    provider = meta["provider"]
    model_id = meta["id"]

    if provider == "anthropic" and _is_key_configured("ANTHROPIC_API_KEY"):
        return _call_anthropic_text(
            api_model=meta.get("api_model", "claude-sonnet-4-20250514"),
            system_instruction=system_instruction,
            user_prompt=user_prompt,
            temperature=temperature,
        )

    if provider == "openai" and _is_key_configured("OPENAI_API_KEY"):
        return _call_openai_text(
            api_model=meta.get("api_model", model_id),
            system_instruction=system_instruction,
            user_prompt=user_prompt,
            temperature=temperature,
        )

    gemini_model = model_id if provider == "google" else "gemini-2.5-flash"
    if HAS_GOOGLE_GENAI_SDK:
        client = genai.Client(
            api_key=os.environ.get("GEMINI_API_KEY"),
            http_options={"api_version": "v1beta"},
        )
        response = client.models.generate_content(
            model=gemini_model,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=temperature,
            ),
        )
        return (response.text or "").strip()

    return _call_gemini_rest_text(
        api_model=gemini_model,
        system_instruction=system_instruction,
        user_prompt=user_prompt,
        temperature=temperature,
    )


def generate_text(
    system_instruction: str,
    user_prompt: str,
    model_id: str = "gemini-2.5-flash",
    temperature: float = 0.4,
) -> str:
    global _last_routing_telemetry
    chain = build_model_failover_chain(model_id)
    attempted: List[str] = []
    last_error: Optional[Exception] = None

    for idx, candidate_id in enumerate(chain):
        meta = get_model_meta(candidate_id)
        attempted.append(meta["id"])
        try:
            result = _invoke_single_model_text(
                meta=meta,
                system_instruction=system_instruction,
                user_prompt=user_prompt,
                temperature=temperature,
            )
            _last_routing_telemetry = {
                "requested_model": model_id,
                "executed_model": meta["id"],
                "failover_triggered": idx > 0 or meta["id"] != model_id,
                "attempted_models": attempted,
            }
            return result
        except Exception as err:
            last_error = err
            has_more = idx < len(chain) - 1
            if not (has_more and is_retriable_model_error(err)):
                raise

    if last_error is not None:
        raise last_error
    raise RuntimeError("No callable models available in failover chain.")


def _invoke_single_model_structured(
    meta: Dict[str, str],
    system_instruction: str,
    user_prompt: str,
    response_schema: Type[BaseModel],
    temperature: float,
) -> BaseModel:
    provider = meta["provider"]
    model_id = meta["id"]

    if provider == "anthropic" and _is_key_configured("ANTHROPIC_API_KEY"):
        schema_json = json.dumps(response_schema.model_json_schema(), indent=2)
        augmented_system = (
            f"{system_instruction}\n\n"
            f"Respond ONLY with valid JSON conforming strictly to this JSON Schema:\n{schema_json}"
        )
        raw_text = _call_anthropic_text(
            api_model=meta.get("api_model", "claude-sonnet-4-20250514"),
            system_instruction=augmented_system,
            user_prompt=user_prompt,
            temperature=temperature,
        )
        cleaned = _strip_markdown_fences(raw_text)
        return response_schema.model_validate_json(cleaned)

    if provider == "openai" and _is_key_configured("OPENAI_API_KEY"):
        schema_json = json.dumps(response_schema.model_json_schema(), indent=2)
        augmented_system = (
            f"{system_instruction}\n\n"
            f"Respond ONLY with valid JSON conforming strictly to this JSON Schema:\n{schema_json}"
        )
        raw_text = _call_openai_text(
            api_model=meta.get("api_model", model_id),
            system_instruction=augmented_system,
            user_prompt=user_prompt,
            temperature=temperature,
            json_mode=True,
        )
        cleaned = _strip_markdown_fences(raw_text)
        return response_schema.model_validate_json(cleaned)

    gemini_model = model_id if provider == "google" else "gemini-2.5-pro"
    if HAS_GOOGLE_GENAI_SDK:
        client = genai.Client(
            api_key=os.environ.get("GEMINI_API_KEY"),
            http_options={"api_version": "v1beta"},
        )
        response = client.models.generate_content(
            model=gemini_model,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                response_mime_type="application/json",
                response_schema=response_schema,
                temperature=temperature,
            ),
        )
        return response_schema.model_validate_json(response.text)

    schema_json = json.dumps(response_schema.model_json_schema(), indent=2)
    augmented_system = (
        f"{system_instruction}\n\n"
        f"Respond ONLY with valid JSON conforming strictly to this JSON Schema:\n{schema_json}"
    )
    raw_text = _call_gemini_rest_text(
        api_model=gemini_model,
        system_instruction=augmented_system,
        user_prompt=user_prompt,
        temperature=temperature,
        json_mode=True,
    )
    cleaned = _strip_markdown_fences(raw_text)
    return response_schema.model_validate_json(cleaned)


def generate_structured(
    system_instruction: str,
    user_prompt: str,
    response_schema: Type[BaseModel],
    model_id: str = "gemini-2.5-pro",
    temperature: float = 0.1,
) -> BaseModel:
    global _last_routing_telemetry
    chain = build_model_failover_chain(model_id)
    attempted: List[str] = []
    last_error: Optional[Exception] = None

    for idx, candidate_id in enumerate(chain):
        meta = get_model_meta(candidate_id)
        attempted.append(meta["id"])
        try:
            result = _invoke_single_model_structured(
                meta=meta,
                system_instruction=system_instruction,
                user_prompt=user_prompt,
                response_schema=response_schema,
                temperature=temperature,
            )
            _last_routing_telemetry = {
                "requested_model": model_id,
                "executed_model": meta["id"],
                "failover_triggered": idx > 0 or meta["id"] != model_id,
                "attempted_models": attempted,
            }
            return result
        except Exception as err:
            last_error = err
            has_more = idx < len(chain) - 1
            if not (has_more and is_retriable_model_error(err)):
                raise

    if last_error is not None:
        raise last_error
    raise RuntimeError("No callable models available in structured failover chain.")


def _strip_markdown_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    return cleaned


def _call_gemini_rest_text(
    api_model: str,
    system_instruction: str,
    user_prompt: str,
    temperature: float = 0.2,
    json_mode: bool = False,
) -> str:
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{api_model}:generateContent?key={api_key}"
    generation_config: Dict[str, Any] = {"temperature": temperature}
    if json_mode:
        generation_config["responseMimeType"] = "application/json"

    payload: Dict[str, Any] = {
        "systemInstruction": {"parts": [{"text": system_instruction}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": generation_config,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["candidates"][0]["content"]["parts"][0]["text"].strip()


def _call_openai_text(
    api_model: str,
    system_instruction: str,
    user_prompt: str,
    temperature: float = 0.2,
    json_mode: bool = False,
) -> str:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    payload: Dict[str, Any] = {
        "model": api_model,
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": user_prompt},
        ],
    }
    if not api_model.startswith("o3"):
        payload["temperature"] = temperature
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"].strip()


def _call_anthropic_text(
    api_model: str,
    system_instruction: str,
    user_prompt: str,
    temperature: float = 0.2,
) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    payload = {
        "model": api_model,
        "max_tokens": 4096,
        "temperature": temperature,
        "system": system_instruction,
        "messages": [{"role": "user", "content": user_prompt}],
    }
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["content"][0]["text"].strip()
