import json
import os
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
    },
    {
        "id": "gemini-2.5-pro",
        "label": "Google Gemini 2.5 Pro (Deep Compliance Reasoning)",
        "provider": "google",
        "env_key": "GEMINI_API_KEY",
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


def _is_key_configured(env_key: str) -> bool:
    val = (os.environ.get(env_key) or "").strip()
    if not val:
        return False
    placeholder_prefixes = ("your_", "optional_", "replace_", "insert_")
    return not val.lower().startswith(placeholder_prefixes)


def list_available_models() -> List[Dict[str, Any]]:
    """
    Returns model metadata along with whether the corresponding API key is configured in .env.
    """
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


def generate_text(
    system_instruction: str,
    user_prompt: str,
    model_id: str = "gemini-2.5-flash",
    temperature: float = 0.4,
) -> str:
    """
    Adapter method to generate a draft or remediated text across Google Gemini,
    Anthropic Claude, or OpenAI models. Falls back gracefully to Gemini if a secondary
    provider key is not configured.
    """
    meta = get_model_meta(model_id)
    provider = meta["provider"]

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

    # Default to Google Gemini
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


def generate_structured(
    system_instruction: str,
    user_prompt: str,
    response_schema: Type[BaseModel],
    model_id: str = "gemini-2.5-pro",
    temperature: float = 0.1,
) -> BaseModel:
    """
    Adapter method to generate strict Pydantic-validated JSON across supported providers.
    """
    meta = get_model_meta(model_id)
    provider = meta["provider"]

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

    # Default to Google GenAI SDK with native response_schema
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
