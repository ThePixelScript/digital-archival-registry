"""Search endpoint routing for full-text, fuzzy, and faceted queries and exports."""

from datetime import datetime, timezone
import math
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from elasticsearch import Elasticsearch
from app.core.elasticsearch import get_elasticsearch_client
from app.models.artifact import ArtifactSearchResult, SearchResponse
from app.services.artifact_service import (
    ElasticsearchServiceError,
    search_artifacts,
    stream_artifacts_csv,
)

router = APIRouter(prefix="/search", tags=["Search"])


@router.get("", response_model=SearchResponse, summary="Search artifacts across multiple fields with relevance ranking")
def search_collection(
    q: Optional[str] = Query(default=None, description="Search terms to match across artifact names, descriptions, and history"),
    category: Optional[str] = Query(default=None, description="Exact-match filter by artifact category"),
    historical_period: Optional[str] = Query(default=None, description="Exact-match filter by historical period"),
    condition: Optional[str] = Query(default=None, description="Exact-match filter by conservation condition"),
    is_on_display: Optional[bool] = Query(default=None, description="Filter by display status"),
    origin_culture: Optional[str] = Query(default=None, description="Filter by civilization or cultural origin"),
    sort_by: Optional[str] = Query(
        default=None,
        description="Field to sort by (estimated_value_usd, acquisition_date, creation_year_earliest, name, created_at)",
    ),
    sort_order: str = Query(default="asc", pattern="^(asc|desc)$", description="Sort direction ('asc' or 'desc')"),
    fuzzy: bool = Query(default=False, description="Enable Levenshtein typo-tolerant fuzzy matching"),
    page: int = Query(default=1, ge=1, description="Page number"),
    page_size: int = Query(default=10, ge=1, le=100, description="Items per page"),
    client: Elasticsearch = Depends(get_elasticsearch_client),
) -> SearchResponse:
    """Executes a relevance-ranked full-text search against the Elasticsearch inverted index."""
    try:
        items, total, took_ms = search_artifacts(
            client=client,
            query_text=q,
            category=category,
            historical_period=historical_period,
            condition=condition,
            is_on_display=is_on_display,
            origin_culture=origin_culture,
            sort_by=sort_by,
            sort_order=sort_order,
            fuzzy=fuzzy,
            page=page,
            page_size=page_size,
        )
        total_pages = math.ceil(total / page_size) if total > 0 else 0
        return SearchResponse(
            items=[ArtifactSearchResult(**item) for item in items],
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
            query=q,
            fuzzy_applied=fuzzy,
            took_ms=took_ms,
        )
    except ElasticsearchServiceError as ex:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(ex))


@router.get(
    "/export",
    summary="Download complete matching search results as a CSV spreadsheet",
    response_description="A streaming CSV file containing all matching artifact records",
)
def export_search_csv(
    q: Optional[str] = Query(default=None, description="Search terms to match across artifact names, descriptions, and history"),
    category: Optional[str] = Query(default=None, description="Exact-match filter by artifact category"),
    historical_period: Optional[str] = Query(default=None, description="Exact-match filter by historical period"),
    condition: Optional[str] = Query(default=None, description="Exact-match filter by conservation condition"),
    is_on_display: Optional[bool] = Query(default=None, description="Filter by display status"),
    origin_culture: Optional[str] = Query(default=None, description="Filter by civilization or cultural origin"),
    sort_by: Optional[str] = Query(
        default=None,
        description="Field to sort by (estimated_value_usd, acquisition_date, creation_year_earliest, name, created_at)",
    ),
    sort_order: str = Query(default="asc", pattern="^(asc|desc)$", description="Sort direction ('asc' or 'desc')"),
    fuzzy: bool = Query(default=False, description="Enable Levenshtein typo-tolerant fuzzy matching"),
    client: Elasticsearch = Depends(get_elasticsearch_client),
) -> StreamingResponse:
    """Streams a CSV spreadsheet export of matching search results."""
    try:
        csv_stream = stream_artifacts_csv(
            client=client,
            query_text=q,
            category=category,
            historical_period=historical_period,
            condition=condition,
            is_on_display=is_on_display,
            origin_culture=origin_culture,
            sort_by=sort_by,
            sort_order=sort_order,
            fuzzy=fuzzy,
        )
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"museum_artifacts_search_{timestamp}.csv"
        headers = {
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Access-Control-Expose-Headers": "Content-Disposition",
        }
        return StreamingResponse(
            csv_stream,
            media_type="text/csv; charset=utf-8",
            headers=headers,
        )
    except ElasticsearchServiceError as ex:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(ex))
