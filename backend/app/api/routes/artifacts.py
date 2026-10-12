"""Artifact management routes for CRUD operations and streaming CSV exports."""

from datetime import datetime, timezone
import math
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from elasticsearch import Elasticsearch
from app.core.elasticsearch import get_elasticsearch_client
from app.models.artifact import (
    ArtifactCreate,
    ArtifactListResponse,
    ArtifactResponse,
    ArtifactUpdate,
)
from app.services.artifact_service import (
    ArtifactAlreadyExistsError,
    ArtifactNotFoundError,
    ElasticsearchServiceError,
    create_artifact,
    delete_artifact,
    get_artifact_by_id,
    list_artifacts,
    stream_artifacts_csv,
    update_artifact,
)

router = APIRouter(prefix="/artifacts", tags=["Artifacts"])


@router.get("", response_model=ArtifactListResponse, summary="List artifacts with pagination and filters")
def list_all_artifacts(
    page: int = Query(default=1, ge=1, description="Page number"),
    page_size: int = Query(default=10, ge=1, le=100, description="Items per page"),
    category: Optional[str] = Query(default=None, description="Filter by category"),
    historical_period: Optional[str] = Query(default=None, description="Filter by historical period"),
    condition: Optional[str] = Query(default=None, description="Filter by conservation condition"),
    is_on_display: Optional[bool] = Query(default=None, description="Filter by display status"),
    client: Elasticsearch = Depends(get_elasticsearch_client),
) -> ArtifactListResponse:
    """Lists archival artifact records with exact-match filtering and pagination."""
    try:
        items, total = list_artifacts(
            client=client,
            page=page,
            page_size=page_size,
            category=category,
            historical_period=historical_period,
            condition=condition,
            is_on_display=is_on_display,
        )
        total_pages = math.ceil(total / page_size) if total > 0 else 0
        return ArtifactListResponse(
            items=[ArtifactResponse(**item) for item in items],
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
        )
    except ElasticsearchServiceError as ex:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(ex))


@router.get(
    "/export",
    summary="Download complete matching artifact records as a CSV spreadsheet",
    response_description="A streaming CSV file containing all matching artifact records",
)
def export_artifacts_csv(
    q: Optional[str] = Query(default=None, description="Search terms to match across artifact names, descriptions, and history"),
    category: Optional[str] = Query(default=None, description="Filter by category"),
    historical_period: Optional[str] = Query(default=None, description="Filter by historical period"),
    condition: Optional[str] = Query(default=None, description="Filter by conservation condition"),
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
    """Streams a CSV spreadsheet export of the complete matching result set.

    Bypasses standard pagination and Elasticsearch max_result_window limits using
    the scroll API. Features RFC 4180 compliant escaping, Unicode preservation,
    and spreadsheet formula-injection neutralization (CWE-1236).
    """
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
        filename = f"museum_artifacts_export_{timestamp}.csv"
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


@router.get("/{artifact_id}", response_model=ArtifactResponse, summary="Get an artifact by document ID")
def get_single_artifact(
    artifact_id: str,
    client: Elasticsearch = Depends(get_elasticsearch_client),
) -> ArtifactResponse:
    """Retrieves a single artifact record by its identifier."""
    try:
        doc = get_artifact_by_id(client, artifact_id)
        return ArtifactResponse(**doc)
    except ArtifactNotFoundError as ex:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(ex))
    except ElasticsearchServiceError as ex:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(ex))


@router.post("", response_model=ArtifactResponse, status_code=status.HTTP_201_CREATED, summary="Create a new artifact")
def create_new_artifact(
    artifact_in: ArtifactCreate,
    client: Elasticsearch = Depends(get_elasticsearch_client),
) -> ArtifactResponse:
    """Registers a new artifact record into the archival index using create-only indexing."""
    try:
        created = create_artifact(client, artifact_in.model_dump())
        return ArtifactResponse(**created)
    except ArtifactAlreadyExistsError as ex:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(ex))
    except ElasticsearchServiceError as ex:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(ex))


@router.put("/{artifact_id}", response_model=ArtifactResponse, summary="Update an existing artifact")
def update_existing_artifact(
    artifact_id: str,
    artifact_in: ArtifactUpdate,
    client: Elasticsearch = Depends(get_elasticsearch_client),
) -> ArtifactResponse:
    """Updates an existing artifact by merging provided non-null fields with existing attributes."""
    try:
        update_dict = artifact_in.model_dump(exclude_unset=True)
        updated = update_artifact(client, artifact_id, update_dict)
        return ArtifactResponse(**updated)
    except ArtifactNotFoundError as ex:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(ex))
    except ElasticsearchServiceError as ex:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(ex))


@router.delete("/{artifact_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete an artifact")
def delete_single_artifact(
    artifact_id: str,
    client: Elasticsearch = Depends(get_elasticsearch_client),
) -> None:
    """Permanently deletes an artifact record from the archival index."""
    try:
        delete_artifact(client, artifact_id)
    except ArtifactNotFoundError as ex:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(ex))
    except ElasticsearchServiceError as ex:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(ex))
