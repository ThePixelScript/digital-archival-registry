"""Artifact management routes for CRUD operations and streaming CSV exports."""

from datetime import datetime, timezone
import math
from typing import Optional
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from elasticsearch import Elasticsearch
from app.core.elasticsearch import get_elasticsearch_client
from app.models.artifact import (
    ArtifactCreate,
    ArtifactListResponse,
    ArtifactResponse,
    ArtifactUpdate,
    CSVImportPreviewResponse,
    CSVImportResultResponse,
    CSVRowError,
)
from app.services.artifact_service import (
    CSV_IMPORT_PREVIEW_LIMIT,
    MAX_IMPORT_FILE_SIZE_BYTES,
    ArtifactAlreadyExistsError,
    ArtifactNotFoundError,
    ElasticsearchServiceError,
    create_artifact,
    delete_artifact,
    execute_csv_import,
    get_artifact_by_id,
    list_artifacts,
    stream_artifacts_csv,
    update_artifact,
    validate_csv_import,
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


@router.post(
    "/import/preview",
    response_model=CSVImportPreviewResponse,
    summary="Preview and validate artifact CSV upload without writing to Elasticsearch",
)
async def preview_csv_import(
    file: UploadFile = File(..., description="CSV file matching archival schema (RFC 4180)"),
    client: Elasticsearch = Depends(get_elasticsearch_client),
) -> CSVImportPreviewResponse:
    """Performs dry-run validation of an uploaded CSV file.

    Checks schema conformity, types, ranges, in-file duplicates, and conflicts
    against current collection records in Elasticsearch without performing mutations.
    """
    file_bytes = await file.read()
    if len(file_bytes) > MAX_IMPORT_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Uploaded file exceeds the maximum size limit of {MAX_IMPORT_FILE_SIZE_BYTES // (1024 * 1024)} MB.",
        )

    try:
        valid_records, errors, dup_file, dup_es, total_rows, error_rows = validate_csv_import(client, file_bytes)
        can_import = (len(errors) == 0 and len(valid_records) > 0 and total_rows > 0)
        return CSVImportPreviewResponse(
            total_rows=total_rows,
            valid_rows=len(valid_records),
            error_rows=error_rows,
            duplicate_file_ids=dup_file,
            existing_collection_ids=dup_es,
            errors=errors,
            can_import=can_import,
            preview_records=valid_records[:CSV_IMPORT_PREVIEW_LIMIT],
        )
    except ElasticsearchServiceError as ex:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(ex))


@router.post(
    "/import",
    response_model=CSVImportResultResponse,
    summary="Confirm and execute bulk import of artifact records into Elasticsearch",
)
async def confirm_csv_import(
    file: UploadFile = File(..., description="CSV file previously validated in preview"),
    client: Elasticsearch = Depends(get_elasticsearch_client),
) -> CSVImportResultResponse:
    """Executes bulk indexing of validated artifact records.

    Revalidates the submitted file against Elasticsearch to detect any changes
    since preview, then applies create-only bulk indexing (_op_type="create")
    guaranteeing that existing records are never overwritten.
    """
    file_bytes = await file.read()
    if len(file_bytes) > MAX_IMPORT_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Uploaded file exceeds the maximum size limit of {MAX_IMPORT_FILE_SIZE_BYTES // (1024 * 1024)} MB.",
        )

    try:
        success, imported_count, total_rows, imported_ids, msg, errors = execute_csv_import(client, file_bytes)
        if not success:
            status_code = (
                status.HTTP_409_CONFLICT
                if any("already exists" in e.message.lower() or "conflict" in e.message.lower() for e in errors)
                else status.HTTP_400_BAD_REQUEST
            )
            raise HTTPException(
                status_code=status_code,
                detail={
                    "message": msg,
                    "total_rows": total_rows,
                    "imported_count": imported_count,
                    "errors": [e.model_dump() for e in errors],
                },
            )

        return CSVImportResultResponse(
            success=True,
            imported_count=imported_count,
            total_rows=total_rows,
            imported_ids=imported_ids,
            message=msg,
            errors=[],
        )
    except HTTPException:
        raise
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
