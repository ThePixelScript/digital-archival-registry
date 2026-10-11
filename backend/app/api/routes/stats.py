"""Collection statistics and aggregation routes."""

from fastapi import APIRouter, Depends, HTTPException, status
from elasticsearch import Elasticsearch
from app.core.elasticsearch import get_elasticsearch_client
from app.models.artifact import CollectionStatsResponse
from app.services.artifact_service import ElasticsearchServiceError, get_collection_stats

router = APIRouter(prefix="/stats", tags=["Statistics"])


@router.get("", response_model=CollectionStatsResponse, summary="Get collection statistics and aggregation facets")
def get_stats_summary(
    client: Elasticsearch = Depends(get_elasticsearch_client),
) -> CollectionStatsResponse:
    """Returns faceted terms aggregations and valuation metrics for the collection."""
    try:
        stats = get_collection_stats(client)
        return CollectionStatsResponse(**stats)
    except ElasticsearchServiceError as ex:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(ex))
