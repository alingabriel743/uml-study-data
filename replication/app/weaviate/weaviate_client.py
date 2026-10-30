"""
Weaviate client for diagrams-v2.

Used in local Docker mode (APP_WEAVIATE_ENABLED=true).
On deployed environments the KB service handles vectorisation instead.
"""

from __future__ import annotations

from typing import Optional

from app.utils.logger import get_logger

logger = get_logger(__name__)

_weaviate_client = None


def get_weaviate_client():
    """Return a connected Weaviate client (singleton)."""
    global _weaviate_client

    try:
        import weaviate
        from weaviate.connect import ConnectionParams
    except ImportError as exc:
        raise ImportError(
            "weaviate-client is not installed. "
            "Add it to requirements.txt: weaviate-client>=4.0.0"
        ) from exc

    from app.utils.config import settings

    try:
        if _weaviate_client is None:
            conn = ConnectionParams.from_params(
                http_host=settings.weaviate_http_host,
                http_port=settings.weaviate_http_port,
                http_secure=settings.weaviate_http_secure,
                grpc_host=settings.weaviate_grpc_host,
                grpc_port=settings.weaviate_grpc_port,
                grpc_secure=settings.weaviate_grpc_secure,
            )
            _weaviate_client = weaviate.WeaviateClient(conn)
            _weaviate_client.connect()
            logger.info(
                f"Weaviate client connected: "
                f"{settings.weaviate_http_host}:{settings.weaviate_http_port}"
            )
        elif not _weaviate_client.is_ready():
            logger.warning("Weaviate client not ready — reconnecting")
            _weaviate_client.connect()

    except Exception as exc:
        logger.error(f"Failed to initialise Weaviate client: {exc}")
        raise

    return _weaviate_client


def close_weaviate_client() -> None:
    """Close and clean up the global client."""
    global _weaviate_client
    if _weaviate_client is None:
        return
    try:
        _weaviate_client.close()
        logger.info("Weaviate client closed")
    except Exception as exc:
        logger.warning(f"Error closing Weaviate client: {exc}")
    finally:
        _weaviate_client = None
