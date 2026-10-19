"""Elasticsearch connection and lifecycle management module."""

from typing import Any, Dict, Optional
from elasticsearch import Elasticsearch
from app.core.config import settings

_client: Optional[Elasticsearch] = None


def get_elasticsearch_client() -> Elasticsearch:
    """Returns the active Elasticsearch client singleton instance.

    If not yet initialized, creates a client using the configured connection settings.
    Prioritizes ELASTIC_API_KEY when set; otherwise falls back to basic authentication.
    """
    global _client
    if _client is None:
        client_kwargs: Dict[str, Any] = {
            "hosts": [settings.elasticsearch_url],
            "request_timeout": 15,
            "retry_on_timeout": True,
            "max_retries": 3,
        }
        if settings.ELASTIC_API_KEY and settings.ELASTIC_API_KEY.strip():
            client_kwargs["api_key"] = settings.ELASTIC_API_KEY.strip()
        else:
            client_kwargs["basic_auth"] = (settings.ELASTIC_USER, settings.ELASTIC_PASSWORD)

        _client = Elasticsearch(**client_kwargs)
    return _client


def close_elasticsearch_client() -> None:
    """Gracefully closes the active Elasticsearch connection on application shutdown."""
    global _client
    if _client is not None:
        _client.close()
        _client = None
