"""
Helpers para enriquecer eventos de analítica (cliente HTTP, uso LLM) sin acoplar rutas.
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional

from starlette.requests import Request

from .db.analytics_management import (
    InteractionEventInsert,
    LlmInvocationInsert,
    safe_record_interaction_event,
    safe_record_llm_invocation,
)


def client_snapshot_from_request(request: Optional[Request]) -> Dict[str, Any]:
    """Sin PII: hash corto de IP y prefijo de User-Agent."""
    if request is None:
        return {}
    out: Dict[str, Any] = {}
    try:
        client = request.client
        if client and client.host:
            out["ip_hash"] = hashlib.sha256(client.host.encode("utf-8")).hexdigest()[:20]
    except Exception:
        pass
    try:
        ua = request.headers.get("user-agent") or ""
        out["user_agent_prefix"] = ua[:200] if ua else None
    except Exception:
        pass
    return {k: v for k, v in out.items() if v is not None}


def track_event(
    *,
    event_category: str,
    event_name: str,
    user_id: Optional[str] = None,
    request: Optional[Request] = None,
    thread_id: Optional[str] = None,
    assistant_id: Optional[str] = None,
    http_method: Optional[str] = None,
    http_path: Optional[str] = None,
    status_code: Optional[int] = None,
    duration_ms: Optional[int] = None,
    success: Optional[bool] = None,
    error_type: Optional[str] = None,
    error_message: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    metrics: Optional[Dict[str, Any]] = None,
) -> None:
    safe_record_interaction_event(
        InteractionEventInsert(
            event_category=event_category,
            event_name=event_name,
            user_id=user_id,
            thread_id=thread_id,
            assistant_id=assistant_id,
            http_method=http_method,
            http_path=http_path,
            status_code=status_code,
            duration_ms=duration_ms,
            success=success,
            error_type=error_type,
            error_message=error_message,
            metadata=metadata or {},
            metrics=metrics or {},
            client=client_snapshot_from_request(request),
        )
    )


def extract_usage_from_gemini_langchain_messages(messages: List[Any]) -> Dict[str, Any]:
    """
    Intenta leer usage_metadata de mensajes AIMessage (LangChain + Google GenAI).
    """
    total_in = 0
    total_out = 0
    models: List[str] = []
    for msg in messages or []:
        um = getattr(msg, "usage_metadata", None)
        if um is None and hasattr(msg, "response_metadata"):
            rm = getattr(msg, "response_metadata", None) or {}
            if isinstance(rm, dict):
                um = rm.get("usage_metadata") or rm.get("token_usage")
        if not isinstance(um, dict):
            continue
        # Gemini / LC variants
        inp = um.get("input_tokens") or um.get("prompt_token_count") or um.get("input_token_count")
        out = um.get("output_tokens") or um.get("candidates_token_count") or um.get("output_token_count")
        if inp is not None:
            try:
                total_in += int(inp)
            except (TypeError, ValueError):
                pass
        if out is not None:
            try:
                total_out += int(out)
            except (TypeError, ValueError):
                pass
        mid = um.get("model_name") or um.get("model")
        if mid:
            models.append(str(mid))
    return {
        "input_tokens": total_in or None,
        "output_tokens": total_out or None,
        "total_tokens": (total_in + total_out) if (total_in or total_out) else None,
        "models_seen": list(dict.fromkeys(models)) if models else [],
    }


def extract_usage_from_lc_invoke_response(response: Any) -> Dict[str, Any]:
    """Respuesta de model.invoke (ChatGoogleGenerativeAI / BaseMessage)."""
    total_in = total_out = None
    model_name = None
    try:
        rm = getattr(response, "response_metadata", None) or {}
        if isinstance(rm, dict):
            um = rm.get("usage_metadata") or {}
            if isinstance(um, dict):
                total_in = um.get("input_tokens") or um.get("prompt_token_count")
                total_out = um.get("output_tokens") or um.get("candidates_token_count")
            model_name = rm.get("model_name") or rm.get("model")
    except Exception:
        pass
    tin = int(total_in) if total_in is not None else None
    tout = int(total_out) if total_out is not None else None
    return {
        "input_tokens": tin,
        "output_tokens": tout,
        "total_tokens": (tin + tout) if tin is not None and tout is not None else None,
        "model_name": model_name,
    }


def record_llm_call(
    *,
    model_name: str,
    user_id: str,
    thread_id: Optional[str],
    assistant_id: Optional[str],
    provider: str,
    usage: Dict[str, Any],
    latency_ms: Optional[int],
    interaction_event_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    tin = usage.get("input_tokens")
    tout = usage.get("output_tokens")
    tot = usage.get("total_tokens")
    if isinstance(tin, int) and isinstance(tout, int) and tot is None:
        tot = tin + tout
    safe_record_llm_invocation(
        LlmInvocationInsert(
            model_name=model_name,
            user_id=user_id,
            thread_id=thread_id,
            assistant_id=assistant_id,
            interaction_event_id=interaction_event_id,
            provider=provider,
            input_tokens=tin if isinstance(tin, int) else None,
            output_tokens=tout if isinstance(tout, int) else None,
            total_tokens=tot if isinstance(tot, int) else None,
            latency_ms=latency_ms,
            metadata=metadata or {},
        )
    )
