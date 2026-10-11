"""Elasticsearch connection and lifecycle management module."""

from typing import Optional
from elasticsearch import Elasticsearch
from app.core.config import settings

_client: Optional[Elasticsearch] = None


def get_elasticsearch_client() -> Elasticsearch:
    """Returns the active Elasticsearch client singleton instance.

    If not yet initialized, creates a client using the configured connection settings.
    """
    global _client
    if _client is None:
        _client = Elasticsearch(
            hosts=[settings.elasticsearch_url],
            basic_auth=(settings.ELASTIC_USER, settings.ELASTIC_PASSWORD),
            request_timeout=15,
            retry_on_timeout=True,
            max_retries=3,
        )
    return _client


def close_elasticsearch_client() -> None:
    """Gracefully closes the active Elasticsearch connection on application shutdown."""
    global _client
    if _client is not None:
        _client.close()
        _client = None
