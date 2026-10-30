"""
Usage Reporter — sends token consumption data to an external tracking endpoint.
Matches v1 (diagrams/app/utils/usage_reporter.py) pattern exactly.

Configured via settings:
  APP_USAGE_RECORD_URL     — endpoint URL (default: production URL)
  APP_JWT_TOKEN_URL        — JWT token endpoint
  APP_JWT_CLIENT_ID        — service client ID
  APP_JWT_CLIENT_SECRET    — service client secret

In local environment (APP_LOCAL_ENVIRONMENT=true), recording is skipped.
"""

import logging
from typing import Dict, Any, Optional

import requests

from app.utils.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Default production URL (same as v1)
DEFAULT_USAGE_URL = "https://codeiq.ness-ie-practice.com/api/usage/record"
SOURCE_MODULE = "ARCHITECTURE_ANALYZER"  # Matches v1 source_module name


def _get_jwt_token() -> Optional[str]:
    """Get JWT token for service-to-service authentication."""
    jwt_token_url = getattr(settings, 'jwt_token_url', '')
    jwt_client_id = getattr(settings, 'jwt_client_id', '')
    jwt_client_secret = getattr(settings, 'jwt_client_secret', '')

    if not all([jwt_token_url, jwt_client_id, jwt_client_secret]):
        logger.debug("[USAGE_REPORTER] JWT config not set — skipping token fetch")
        return None

    try:
        response = requests.post(
            jwt_token_url,
            headers={"Content-Type": "application/json"},
            json={"clientId": jwt_client_id, "clientSecret": jwt_client_secret},
            timeout=30,
        )
        response.raise_for_status()
        token_data = response.json()
        access_token = token_data.get("accessToken") or token_data.get("access_token")
        if access_token:
            logger.debug("[USAGE_REPORTER] JWT token obtained")
            return access_token
        logger.error("[USAGE_REPORTER] No accessToken in JWT response")
        return None
    except Exception as e:
        logger.error(f"[USAGE_REPORTER] Failed to get JWT token: {e}")
        return None


def record_usage(
    user_id: str,
    organization_id: str,
    model_id: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    source_module: str = SOURCE_MODULE,
) -> bool:
    """
    Record Bedrock/Claude usage to the external tracking endpoint.
    Skipped automatically in local environment.

    Payload format matches v1:
    {
        "userId": "...",
        "organizationId": "...",
        "modelId": "...",
        "inputTokens": N,
        "outputTokens": N,
        "cacheReadTokens": N,       (optional)
        "cacheCreationTokens": N,   (optional)
        "sourceModule": "..."       (optional)
    }
    """
    if settings.local_environment:
        logger.debug("[USAGE_REPORTER] Local environment — skipping usage recording")
        return True

    if input_tokens == 0 and output_tokens == 0:
        logger.debug("[USAGE_REPORTER] Zero tokens — skipping")
        return True

    access_token = _get_jwt_token()
    if not access_token:
        logger.warning("[USAGE_REPORTER] No JWT token — cannot record usage")
        return False

    url = getattr(settings, 'usage_record_url', DEFAULT_USAGE_URL) or DEFAULT_USAGE_URL

    payload: Dict[str, Any] = {
        "userId": user_id,
        "organizationId": organization_id,
        "modelId": model_id,
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
    }
    if source_module:
        payload["sourceModule"] = source_module
    if cache_read_tokens > 0:
        payload["cacheReadTokens"] = cache_read_tokens
    if cache_write_tokens > 0:
        payload["cacheCreationTokens"] = cache_write_tokens

    logger.info(f"[USAGE_REPORTER] Recording usage → {url}")
    logger.info(f"[USAGE_REPORTER] Tokens: in={input_tokens:,} out={output_tokens:,} cache_read={cache_read_tokens:,}")

    try:
        response = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        logger.info("[USAGE_REPORTER] Usage recorded successfully")
        return True
    except requests.RequestException as e:
        logger.error(f"[USAGE_REPORTER] Failed to record usage: {e}")
        return False
    except Exception as e:
        logger.error(f"[USAGE_REPORTER] Unexpected error: {e}")
        return False


def record_usage_from_agent_messages(
    messages: list,
    user_id: str,
    organization_id: str,
    model_id: Optional[str] = None,
) -> bool:
    """
    Extract token usage from agent result messages and record it.
    Called after diagram generation to report Bedrock consumption.

    Args:
        messages: List of agent message dicts (from orchestrator)
        user_id: User identifier (from v1 request)
        organization_id: Organization identifier (from v1 request)
        model_id: Override model ID (defaults to settings.bedrock_model_id)
    """
    if settings.local_environment:
        return True

    effective_model = model_id or settings.bedrock_model_id
    total_input = total_output = total_cache_read = total_cache_write = 0

    for msg in messages:
        if msg.get("type") == "result":
            usage = msg.get("usage", {})
            total_input += usage.get("input_tokens", 0)
            total_output += usage.get("output_tokens", 0)
            total_cache_read += usage.get("cache_read_input_tokens", 0)
            total_cache_write += usage.get("cache_creation_input_tokens", 0)

    if total_input == 0 and total_output == 0:
        logger.debug("[USAGE_REPORTER] No token usage found in messages")
        return True

    return record_usage(
        user_id=user_id,
        organization_id=organization_id,
        model_id=effective_model,
        input_tokens=total_input,
        output_tokens=total_output,
        cache_read_tokens=total_cache_read,
        cache_write_tokens=total_cache_write,
    )
