"""Health check endpoint route handler."""

from fastapi import APIRouter, Depends
from elasticsearch import Elasticsearch
from app.core.config import settings
from app.core.elasticsearch import get_elasticsearch_client
from app.models.artifact import HealthResponse
from app.services.artifact_service import get_health_status

router = APIRouter(tags=["Health"])


@router.get("/health", response_model=HealthResponse, summary="Service & cluster health check")
def health_check(
    client: Elasticsearch = Depends(get_elasticsearch_client),
) -> HealthResponse:
    """Returns application status and Elasticsearch cluster connectivity metadata."""
    es_status = get_health_status(client)
    overall_status = "ok" if es_status.get("connected") else "degraded"
    return HealthResponse(
        status=overall_status,
        environment=settings.ENVIRONMENT,
        elasticsearch=es_status,
    )
