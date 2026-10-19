"""Elasticsearch service module encapsulating artifact query generation, search, and CRUD operations."""

import csv
from datetime import datetime, timezone
import io
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple
from elasticsearch import Elasticsearch, ConflictError, NotFoundError, helpers
from pydantic import ValidationError
from app.core.config import settings
from app.models.artifact import ArtifactCreate, CSVRowError, Dimensions


class ArtifactNotFoundError(Exception):
    """Raised when an artifact document does not exist in Elasticsearch."""
    pass


class ArtifactAlreadyExistsError(Exception):
    """Raised when attempting to create an artifact with an existing document ID."""
    pass


class ElasticsearchServiceError(Exception):
    """Raised on unexpected Elasticsearch transport or server failures."""
    pass


def _format_hit(hit: Dict[str, Any], include_score: bool = False) -> Dict[str, Any]:
    """Formats an Elasticsearch hit into an artifact dictionary with optional relevance score."""
    source = dict(hit["_source"])
    if include_score:
        source["relevance_score"] = hit.get("_score")
    return source


def get_artifact_by_id(client: Elasticsearch, artifact_id: str) -> Dict[str, Any]:
    """Retrieves a single artifact by its document ID.

    Raises:
        ArtifactNotFoundError: If document does not exist.
        ElasticsearchServiceError: On cluster communication failure.
    """
    try:
        response = client.get(index=settings.ELASTICSEARCH_INDEX, id=artifact_id)
        return _format_hit(response)
    except NotFoundError:
        raise ArtifactNotFoundError(f"Artifact with ID '{artifact_id}' was not found.")
    except Exception as ex:
        raise ElasticsearchServiceError(f"Error retrieving artifact '{artifact_id}': {ex}")


def list_artifacts(
    client: Elasticsearch,
    page: int = 1,
    page_size: int = 10,
    category: Optional[str] = None,
    historical_period: Optional[str] = None,
    condition: Optional[str] = None,
    is_on_display: Optional[bool] = None,
) -> Tuple[List[Dict[str, Any]], int]:
    """Lists artifacts with pagination and exact-match keyword filtering."""
    filters = []
    if category:
        filters.append({"term": {"category": category}})
    if historical_period:
        filters.append({"term": {"historical_period": historical_period}})
    if condition:
        filters.append({"term": {"condition": condition}})
    if is_on_display is not None:
        filters.append({"term": {"is_on_display": is_on_display}})

    query: Dict[str, Any] = {"bool": {"must": [{"match_all": {}}]}}
    if filters:
        query["bool"]["filter"] = filters

    from_val = (page - 1) * page_size
    try:
        response = client.search(
            index=settings.ELASTICSEARCH_INDEX,
            query=query,
            from_=from_val,
            size=page_size,
            sort=[{"creation_year_earliest": {"order": "asc"}}],
        )
        total = response["hits"]["total"]["value"]
        items = [_format_hit(h) for h in response["hits"]["hits"]]
        return items, total
    except Exception as ex:
        raise ElasticsearchServiceError(f"Error listing artifacts: {ex}")


CSV_COLUMNS: List[str] = [
    "artifact_id",
    "accession_number",
    "name",
    "alternate_names",
    "category",
    "historical_period",
    "origin_culture",
    "origin_location",
    "creation_year_earliest",
    "creation_year_latest",
    "medium_materials",
    "dimensions_height_cm",
    "dimensions_width_cm",
    "dimensions_depth_cm",
    "dimensions_weight_kg",
    "condition",
    "is_on_display",
    "storage_location",
    "estimated_value_usd",
    "acquisition_date",
    "acquisition_method",
    "description",
    "historical_context",
    "provenance",
    "restoration_notes",
    "tags",
    "created_at",
    "updated_at",
]

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "%")


def sanitize_csv_value(val: Any) -> Any:
    """Sanitizes values for CSV export, protecting against spreadsheet formula injection (CWE-1236).

    Prepends an apostrophe (') to untrusted text starting with formula triggers ('=', '+', '-', '@', '\t', '\r', '%')
    so spreadsheets (Excel, Calc, Sheets) treat them as plain strings rather than formulas.
    Genuine numeric types (including negative historical years and float valuations), booleans, and nulls
    are preserved intact without corruption.
    """
    if val is None:
        return ""
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, (int, float)):
        return val
    if isinstance(val, list):
        sanitized_items = [sanitize_csv_value(item) for item in val if item is not None]
        val_str = ", ".join(str(item) for item in sanitized_items)
    else:
        val_str = str(val)

    if val_str and val_str[0] in FORMULA_PREFIXES:
        return f"'{val_str}"
    stripped = val_str.lstrip(" ")
    if stripped and stripped[0] in FORMULA_PREFIXES:
        return f"'{val_str}"
    return val_str


CSV_REQUIRED_COLUMNS: List[str] = [
    "artifact_id",
    "accession_number",
    "name",
    "category",
    "historical_period",
    "origin_culture",
    "origin_location",
    "creation_year_earliest",
    "creation_year_latest",
    "medium_materials",
    "condition",
    "storage_location",
    "estimated_value_usd",
    "acquisition_date",
    "acquisition_method",
    "description",
    "historical_context",
    "provenance",
    "restoration_notes",
]

MAX_IMPORT_FILE_SIZE_BYTES = 5 * 1024 * 1024
MAX_IMPORT_ROW_COUNT = 1000
CSV_IMPORT_PREVIEW_LIMIT = 10


def unescape_csv_value(val: Any) -> Any:
    """Reverses spreadsheet formula injection escaping applied during export.

    If a text value was prepended with an apostrophe (') because it began with
    a formula character (=, +, -, @, \\t, \\r, %), strips the single leading apostrophe
    to recover the authentic archival text without data corruption.
    """
    if val is None:
        return ""
    if not isinstance(val, str):
        return val
    if val.startswith("'"):
        remainder = val[1:]
        stripped = remainder.lstrip(" ")
        if (remainder and remainder[0] in FORMULA_PREFIXES) or (stripped and stripped[0] in FORMULA_PREFIXES):
            return remainder
    return val


def parse_csv_boolean(val: Any) -> bool:
    """Parses a CSV string or boolean representation into a boolean value."""
    if isinstance(val, bool):
        return val
    if val is None:
        return True
    s = str(val).strip().lower()
    if not s:
        return True
    if s in ("true", "1", "yes", "t", "y"):
        return True
    if s in ("false", "0", "no", "f", "n"):
        return False
    raise ValueError(f"Invalid boolean value: '{val}'. Expected 'true' or 'false'.")


def parse_csv_int(val: Any, field_name: str) -> int:
    """Parses an integer field from CSV with range validation."""
    if val is None or (isinstance(val, str) and not val.strip()):
        raise ValueError(f"{field_name} is required")
    s = unescape_csv_value(str(val).strip())
    try:
        val_int = int(s)
    except ValueError:
        raise ValueError(f"Invalid integer value for {field_name}: '{val}'")
    if val_int < -10000 or val_int > 2100:
        raise ValueError(f"{field_name} must be between -10000 and 2100")
    return val_int


def parse_csv_float(val: Any, field_name: str, required: bool = True) -> Optional[float]:
    """Parses a floating-point number from CSV with non-negative validation."""
    if val is None or (isinstance(val, str) and not val.strip()):
        if required:
            raise ValueError(f"{field_name} is required")
        return None
    s = unescape_csv_value(str(val).strip())
    try:
        val_float = float(s)
    except ValueError:
        raise ValueError(f"Invalid decimal number for {field_name}: '{val}'")
    if val_float < 0:
        raise ValueError(f"{field_name} must be greater than or equal to 0")
    return val_float


def parse_csv_list(val: Any) -> List[str]:
    """Splits a comma-separated CSV string into trimmed, unescaped list items."""
    if val is None:
        return []
    s = str(val).strip()
    if not s:
        return []
    items: List[str] = []
    try:
        parsed_row = next(csv.reader(io.StringIO(s), skipinitialspace=True))
    except Exception:
        parsed_row = s.split(",")
    for item in parsed_row:
        clean = unescape_csv_value(item.strip())
        if clean:
            items.append(clean)
    return items


def artifact_to_csv_row(doc: Dict[str, Any]) -> List[Any]:
    """Flattens an artifact document dictionary into a stable, ordered CSV row."""
    raw_dims = doc.get("dimensions")
    dims: Dict[str, Any] = {}
    if isinstance(raw_dims, dict):
        dims = raw_dims
    elif hasattr(raw_dims, "model_dump"):
        dims = raw_dims.model_dump()
    elif hasattr(raw_dims, "__dict__"):
        dims = raw_dims.__dict__

    return [
        sanitize_csv_value(doc.get("artifact_id")),
        sanitize_csv_value(doc.get("accession_number")),
        sanitize_csv_value(doc.get("name")),
        sanitize_csv_value(doc.get("alternate_names")),
        sanitize_csv_value(doc.get("category")),
        sanitize_csv_value(doc.get("historical_period")),
        sanitize_csv_value(doc.get("origin_culture")),
        sanitize_csv_value(doc.get("origin_location")),
        sanitize_csv_value(doc.get("creation_year_earliest")),
        sanitize_csv_value(doc.get("creation_year_latest")),
        sanitize_csv_value(doc.get("medium_materials")),
        sanitize_csv_value(dims.get("height_cm")),
        sanitize_csv_value(dims.get("width_cm")),
        sanitize_csv_value(dims.get("depth_cm")),
        sanitize_csv_value(dims.get("weight_kg")),
        sanitize_csv_value(doc.get("condition")),
        sanitize_csv_value(doc.get("is_on_display")),
        sanitize_csv_value(doc.get("storage_location")),
        sanitize_csv_value(doc.get("estimated_value_usd")),
        sanitize_csv_value(doc.get("acquisition_date")),
        sanitize_csv_value(doc.get("acquisition_method")),
        sanitize_csv_value(doc.get("description")),
        sanitize_csv_value(doc.get("historical_context")),
        sanitize_csv_value(doc.get("provenance")),
        sanitize_csv_value(doc.get("restoration_notes")),
        sanitize_csv_value(doc.get("tags")),
        sanitize_csv_value(doc.get("created_at")),
        sanitize_csv_value(doc.get("updated_at")),
    ]


def build_artifact_search_query(
    query_text: Optional[str] = None,
    category: Optional[str] = None,
    historical_period: Optional[str] = None,
    condition: Optional[str] = None,
    is_on_display: Optional[bool] = None,
    origin_culture: Optional[str] = None,
    fuzzy: bool = False,
) -> Dict[str, Any]:
    """Constructs the standard Elasticsearch bool query matching search and filter criteria."""
    must_clauses: List[Dict[str, Any]] = []
    filter_clauses: List[Dict[str, Any]] = []

    if query_text and query_text.strip():
        search_terms = query_text.strip()
        search_fields = [
            "name^3",
            "alternate_names^2",
            "description^1.5",
            "historical_context",
            "provenance",
            "restoration_notes",
            "medium_materials",
            "tags^1.5",
        ]
        if fuzzy:
            must_clauses.append({
                "multi_match": {
                    "query": search_terms,
                    "fields": search_fields,
                    "fuzziness": "AUTO",
                    "prefix_length": 2,
                }
            })
        else:
            must_clauses.append({
                "multi_match": {
                    "query": search_terms,
                    "fields": search_fields,
                    "type": "best_fields",
                }
            })
    else:
        must_clauses.append({"match_all": {}})

    if category:
        filter_clauses.append({"term": {"category": category}})
    if historical_period:
        filter_clauses.append({"term": {"historical_period": historical_period}})
    if condition:
        filter_clauses.append({"term": {"condition": condition}})
    if is_on_display is not None:
        filter_clauses.append({"term": {"is_on_display": is_on_display}})
    if origin_culture:
        filter_clauses.append({"term": {"origin_culture": origin_culture}})

    bool_query: Dict[str, Any] = {"must": must_clauses}
    if filter_clauses:
        bool_query["filter"] = filter_clauses

    return bool_query


def build_artifact_sort_spec(
    sort_by: Optional[str] = None,
    sort_order: str = "asc",
    query_text: Optional[str] = None,
) -> List[Any]:
    """Constructs the sort specification for Elasticsearch queries."""
    sort_spec: List[Any] = []
    supported_sort_fields = {
        "estimated_value_usd": "estimated_value_usd",
        "acquisition_date": "acquisition_date",
        "creation_year_earliest": "creation_year_earliest",
        "created_at": "created_at",
        "name": "name.keyword",
    }

    if sort_by and sort_by in supported_sort_fields:
        order = "desc" if sort_order.lower() == "desc" else "asc"
        sort_spec.append({supported_sort_fields[sort_by]: {"order": order}})
    elif not query_text or not query_text.strip():
        sort_spec.append({"creation_year_earliest": {"order": "asc"}})
    else:
        sort_spec.append("_score")

    return sort_spec


def search_artifacts(
    client: Elasticsearch,
    query_text: Optional[str] = None,
    category: Optional[str] = None,
    historical_period: Optional[str] = None,
    condition: Optional[str] = None,
    is_on_display: Optional[bool] = None,
    origin_culture: Optional[str] = None,
    sort_by: Optional[str] = None,
    sort_order: str = "asc",
    fuzzy: bool = False,
    page: int = 1,
    page_size: int = 10,
) -> Tuple[List[Dict[str, Any]], int, int]:
    """Executes multi-field full-text relevance search with optional typo tolerance and filters.

    Returns:
        Tuple of (items, total_count, took_ms).
    """
    bool_query = build_artifact_search_query(
        query_text=query_text,
        category=category,
        historical_period=historical_period,
        condition=condition,
        is_on_display=is_on_display,
        origin_culture=origin_culture,
        fuzzy=fuzzy,
    )
    sort_spec = build_artifact_sort_spec(
        sort_by=sort_by,
        sort_order=sort_order,
        query_text=query_text,
    )

    from_val = (page - 1) * page_size
    try:
        response = client.search(
            index=settings.ELASTICSEARCH_INDEX,
            query={"bool": bool_query},
            sort=sort_spec,
            from_=from_val,
            size=page_size,
        )
        total = response["hits"]["total"]["value"]
        took_ms = response.get("took", 0)
        items = [_format_hit(h, include_score=bool(query_text and query_text.strip())) for h in response["hits"]["hits"]]
        return items, total, took_ms
    except Exception as ex:
        raise ElasticsearchServiceError(f"Error executing search query: {ex}")


def stream_artifacts_csv(
    client: Elasticsearch,
    query_text: Optional[str] = None,
    category: Optional[str] = None,
    historical_period: Optional[str] = None,
    condition: Optional[str] = None,
    is_on_display: Optional[bool] = None,
    origin_culture: Optional[str] = None,
    sort_by: Optional[str] = None,
    sort_order: str = "asc",
    fuzzy: bool = False,
    batch_size: int = 500,
) -> Iterator[str]:
    """Streams a CSV spreadsheet of the complete matching result set via the Elasticsearch scroll API.

    Bypasses Elasticsearch's max_result_window limit to reliably export datasets of any size.
    Uses RFC 4180 escaping and neutralizes spreadsheet formula injection.
    """
    bool_query = build_artifact_search_query(
        query_text=query_text,
        category=category,
        historical_period=historical_period,
        condition=condition,
        is_on_display=is_on_display,
        origin_culture=origin_culture,
        fuzzy=fuzzy,
    )
    sort_spec = list(build_artifact_sort_spec(
        sort_by=sort_by,
        sort_order=sort_order,
        query_text=query_text,
    ))
    # Add artifact_id tiebreaker to guarantee deterministic scrolling order
    sort_spec.append({"artifact_id": {"order": "asc"}})

    scroll_timeout = "2m"
    scroll_id = None
    try:
        first_resp = client.search(
            index=settings.ELASTICSEARCH_INDEX,
            query={"bool": bool_query},
            sort=sort_spec,
            size=batch_size,
            scroll=scroll_timeout,
        )
        scroll_id = first_resp.get("_scroll_id")
    except Exception as ex:
        raise ElasticsearchServiceError(f"Error initializing CSV export search: {ex}")

    def _generator() -> Iterator[str]:
        nonlocal scroll_id
        try:
            # Yield UTF-8 Byte Order Mark (BOM) followed by standard header row
            header_buf = io.StringIO()
            header_writer = csv.writer(header_buf, lineterminator="\r\n")
            header_writer.writerow(CSV_COLUMNS)
            yield "\ufeff" + header_buf.getvalue()

            hits = first_resp.get("hits", {}).get("hits", [])
            while hits:
                batch_buf = io.StringIO()
                batch_writer = csv.writer(batch_buf, lineterminator="\r\n")
                for hit in hits:
                    doc = _format_hit(hit)
                    batch_writer.writerow(artifact_to_csv_row(doc))
                yield batch_buf.getvalue()

                scroll_resp = client.scroll(scroll_id=scroll_id, scroll=scroll_timeout)
                scroll_id = scroll_resp.get("_scroll_id")
                hits = scroll_resp.get("hits", {}).get("hits", [])
        finally:
            if scroll_id:
                try:
                    client.clear_scroll(scroll_id=scroll_id)
                except Exception:
                    pass

    return _generator()


def create_artifact(client: Elasticsearch, artifact_data: Dict[str, Any]) -> Dict[str, Any]:
    """Creates a new artifact record with strict create-only semantics to prevent overwriting.

    Raises:
        ArtifactAlreadyExistsError: If artifact_id already exists (HTTP 409).
        ElasticsearchServiceError: On other indexing errors.
    """
    doc_id = artifact_data["artifact_id"]
    now_iso = datetime.now(timezone.utc).isoformat()

    doc_body = dict(artifact_data)
    doc_body["created_at"] = doc_body.get("created_at") or now_iso
    doc_body["updated_at"] = now_iso

    try:
        client.create(
            index=settings.ELASTICSEARCH_INDEX,
            id=doc_id,
            document=doc_body,
            refresh=True,
        )
        return doc_body
    except ConflictError:
        raise ArtifactAlreadyExistsError(f"Artifact with ID '{doc_id}' already exists.")
    except Exception as ex:
        raise ElasticsearchServiceError(f"Error creating artifact '{doc_id}': {ex}")


def update_artifact(client: Elasticsearch, artifact_id: str, update_data: Dict[str, Any]) -> Dict[str, Any]:
    """Updates an existing artifact by merging modified fields while preserving existing data."""
    existing = get_artifact_by_id(client, artifact_id)

    updated_doc = dict(existing)
    for key, val in update_data.items():
        if val is not None:
            updated_doc[key] = val

    updated_doc["updated_at"] = datetime.now(timezone.utc).isoformat()

    try:
        client.index(
            index=settings.ELASTICSEARCH_INDEX,
            id=artifact_id,
            document=updated_doc,
            refresh=True,
        )
        return updated_doc
    except Exception as ex:
        raise ElasticsearchServiceError(f"Error updating artifact '{artifact_id}': {ex}")


def delete_artifact(client: Elasticsearch, artifact_id: str) -> None:
    """Deletes an artifact by ID.

    Raises:
        ArtifactNotFoundError: If document does not exist.
        ElasticsearchServiceError: On other deletion errors.
    """
    try:
        client.delete(index=settings.ELASTICSEARCH_INDEX, id=artifact_id, refresh=True)
    except NotFoundError:
        raise ArtifactNotFoundError(f"Artifact with ID '{artifact_id}' was not found.")
    except Exception as ex:
        raise ElasticsearchServiceError(f"Error deleting artifact '{artifact_id}': {ex}")


def get_collection_stats(client: Elasticsearch) -> Dict[str, Any]:
    """Computes collection metrics and facet bucket counts using Elasticsearch aggregations."""
    aggs = {
        "categories": {"terms": {"field": "category", "size": 50}},
        "historical_periods": {"terms": {"field": "historical_period", "size": 50}},
        "conditions": {"terms": {"field": "condition", "size": 20}},
        "valuation": {"stats": {"field": "estimated_value_usd"}},
    }

    try:
        response = client.search(
            index=settings.ELASTICSEARCH_INDEX,
            query={"match_all": {}},
            aggs=aggs,
            size=0,
        )
        total_artifacts = response["hits"]["total"]["value"]
        aggregations = response.get("aggregations", {})

        categories = {b["key"]: b["doc_count"] for b in aggregations.get("categories", {}).get("buckets", [])}
        periods = {b["key"]: b["doc_count"] for b in aggregations.get("historical_periods", {}).get("buckets", [])}
        conditions = {b["key"]: b["doc_count"] for b in aggregations.get("conditions", {}).get("buckets", [])}
        val_stats = aggregations.get("valuation", {})

        valuation = {
            "count": val_stats.get("count", 0),
            "min": val_stats.get("min"),
            "max": val_stats.get("max"),
            "avg": round(val_stats.get("avg", 0), 2) if val_stats.get("avg") else 0,
            "sum": round(val_stats.get("sum", 0), 2) if val_stats.get("sum") else 0,
        }

        return {
            "total_artifacts": total_artifacts,
            "categories": categories,
            "historical_periods": periods,
            "conditions": conditions,
            "valuation": valuation,
        }
    except Exception as ex:
        raise ElasticsearchServiceError(f"Error aggregating collection statistics: {ex}")


def get_health_status(client: Elasticsearch) -> Dict[str, Any]:
    """Gathers cluster health and active index metadata without exposing sensitive credentials.

    Compatible with both standard Elasticsearch clusters and Elastic Cloud Serverless.
    Base connectivity is established via client.info() (GET /) and index metrics via client.count().
    Cluster health attributes (cluster_status, number_of_nodes) are populated when available
    (e.g., local development), but omitted/None in Serverless where /_cluster/health returns HTTP 410.
    """
    try:
        # 1. Base connectivity and cluster metadata check (supported across Serverless, Cloud, and local)
        info = client.info()
        cluster_name = info.get("cluster_name")

        # 2. Document count check for primary index
        doc_count = 0
        try:
            count_res = client.count(index=settings.ELASTICSEARCH_INDEX)
            doc_count = count_res.get("count", 0)
        except Exception:
            pass

        # 3. Optional cluster health status (supported locally; Serverless returns HTTP 410)
        cluster_status = None
        number_of_nodes = None
        try:
            health = client.cluster.health()
            cluster_status = health.get("status")
            number_of_nodes = health.get("number_of_nodes")
        except Exception:
            # Leave cluster_status as None on Serverless or when cluster-health API is unavailable
            pass

        return {
            "connected": True,
            "cluster_name": cluster_name,
            "cluster_status": cluster_status,
            "number_of_nodes": number_of_nodes,
            "index_name": settings.ELASTICSEARCH_INDEX,
            "document_count": doc_count,
        }
    except Exception as ex:
        return {
            "connected": False,
            "error": str(ex),
            "index_name": settings.ELASTICSEARCH_INDEX,
            "document_count": 0,
        }


def check_existing_artifact_ids(client: Elasticsearch, ids: List[str]) -> Set[str]:
    """Queries Elasticsearch to identify which artifact IDs already exist."""
    if not ids:
        return set()
    existing: Set[str] = set()
    batch_size = 500
    for i in range(0, len(ids), batch_size):
        chunk = ids[i : i + batch_size]
        try:
            res = client.search(
                index=settings.ELASTICSEARCH_INDEX,
                query={"ids": {"values": chunk}},
                _source=False,
                size=len(chunk),
            )
            for hit in res.get("hits", {}).get("hits", []):
                existing.add(hit["_id"])
        except Exception as ex:
            raise ElasticsearchServiceError(f"Error checking existing artifact IDs in Elasticsearch: {ex}")
    return existing


def validate_csv_import(
    client: Elasticsearch,
    file_bytes: bytes,
) -> Tuple[List[Dict[str, Any]], List[CSVRowError], List[str], List[str], int, int]:
    """Validates an uploaded CSV file against the artifact schema and Elasticsearch index.

    Performs a dry run without modifying Elasticsearch:
    - Verifies file size limit (5 MB) and row limit (1,000 records).
    - Checks required header columns.
    - Validates data types, ranges, dates, dimensions, and booleans per row.
    - Detects duplicate artifact IDs within the CSV file.
    - Queries Elasticsearch to detect IDs already stored in the collection.
    - Collects comprehensive row-level errors.

    Returns:
        Tuple of (valid_records, errors, duplicate_file_ids, existing_collection_ids, total_rows, error_rows).
    """
    if len(file_bytes) > MAX_IMPORT_FILE_SIZE_BYTES:
        err = CSVRowError(
            row_number=1,
            message=f"File size exceeds the {MAX_IMPORT_FILE_SIZE_BYTES // (1024 * 1024)} MB maximum limit.",
        )
        return [], [err], [], [], 0, 0

    try:
        content_str = file_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        err = CSVRowError(row_number=1, message="File encoding error: File content is not valid UTF-8 text.")
        return [], [err], [], [], 0, 0

    if not content_str.strip():
        err = CSVRowError(row_number=1, message="The uploaded CSV file is empty.")
        return [], [err], [], [], 0, 0

    try:
        reader = csv.reader(io.StringIO(content_str))
        header_row = next(reader, None)
    except Exception as ex:
        err = CSVRowError(row_number=1, message=f"CSV formatting error: {ex}")
        return [], [err], [], [], 0, 0

    if not header_row:
        err = CSVRowError(row_number=1, message="The uploaded CSV file contains no header row.")
        return [], [err], [], [], 0, 0

    headers = [h.strip() for h in header_row]
    missing_cols = [c for c in CSV_REQUIRED_COLUMNS if c not in headers]
    if missing_cols:
        err = CSVRowError(
            row_number=1,
            message=f"CSV header is missing required column(s): {', '.join(sorted(missing_cols))}",
        )
        return [], [err], [], [], 0, 0

    dict_reader = csv.DictReader(io.StringIO(content_str))
    all_raw_rows = list(dict_reader)
    total_rows = len(all_raw_rows)

    if total_rows == 0:
        err = CSVRowError(row_number=1, message="The CSV file contains a header but no artifact data rows.")
        return [], [err], [], [], 0, 0

    if total_rows > MAX_IMPORT_ROW_COUNT:
        err = CSVRowError(
            row_number=1,
            message=f"CSV row count ({total_rows}) exceeds the maximum limit of {MAX_IMPORT_ROW_COUNT} records.",
        )
        return [], [err], [], [], total_rows, total_rows

    row_errors: List[CSVRowError] = []
    parsed_records: List[Tuple[int, Dict[str, Any]]] = []
    seen_file_ids: Dict[str, int] = {}
    duplicate_file_ids_set: Set[str] = set()

    for idx, raw_row in enumerate(all_raw_rows, start=2):
        row_has_error = False

        raw_art_id = raw_row.get("artifact_id")
        art_id = None
        if raw_art_id is not None and str(raw_art_id).strip():
            art_id = str(unescape_csv_value(str(raw_art_id).strip()))
            if len(art_id) > 64:
                row_errors.append(CSVRowError(row_number=idx, artifact_id=art_id, field="artifact_id", message="artifact_id exceeds maximum length of 64 characters"))
                row_has_error = True
        else:
            row_errors.append(CSVRowError(row_number=idx, field="artifact_id", message="artifact_id is required"))
            row_has_error = True

        if art_id:
            if art_id in seen_file_ids:
                if art_id not in duplicate_file_ids_set:
                    duplicate_file_ids_set.add(art_id)
                    first_row = seen_file_ids[art_id]
                    row_errors.append(CSVRowError(
                        row_number=first_row,
                        artifact_id=art_id,
                        field="artifact_id",
                        message=f"Duplicate artifact_id '{art_id}' appears multiple times in CSV file.",
                    ))
                row_errors.append(CSVRowError(
                    row_number=idx,
                    artifact_id=art_id,
                    field="artifact_id",
                    message=f"Duplicate artifact_id '{art_id}' found in CSV file (first declared on row {seen_file_ids[art_id]}).",
                ))
                row_has_error = True
            else:
                seen_file_ids[art_id] = idx

        raw_acc = raw_row.get("accession_number")
        acc_num = None
        if raw_acc is not None and str(raw_acc).strip():
            acc_num = str(unescape_csv_value(str(raw_acc).strip()))
            if len(acc_num) > 64:
                row_errors.append(CSVRowError(row_number=idx, artifact_id=art_id, field="accession_number", message="accession_number exceeds maximum length of 64 characters"))
                row_has_error = True
        else:
            row_errors.append(CSVRowError(row_number=idx, artifact_id=art_id, field="accession_number", message="accession_number is required"))
            row_has_error = True

        raw_name = raw_row.get("name")
        name = None
        if raw_name is not None and str(raw_name).strip():
            name = str(unescape_csv_value(str(raw_name).strip()))
            if len(name) > 256:
                row_errors.append(CSVRowError(row_number=idx, artifact_id=art_id, field="name", message="name exceeds maximum length of 256 characters"))
                row_has_error = True
        else:
            row_errors.append(CSVRowError(row_number=idx, artifact_id=art_id, field="name", message="name is required"))
            row_has_error = True

        raw_alt = raw_row.get("alternate_names")
        alt_names = None
        if raw_alt is not None and str(raw_alt).strip():
            alt_names = str(unescape_csv_value(str(raw_alt).strip()))
            if len(alt_names) > 256:
                row_errors.append(CSVRowError(row_number=idx, artifact_id=art_id, field="alternate_names", message="alternate_names exceeds maximum length of 256 characters"))
                row_has_error = True

        raw_cat = raw_row.get("category")
        category = None
        if raw_cat is not None and str(raw_cat).strip():
            category = str(unescape_csv_value(str(raw_cat).strip()))
            if len(category) > 100:
                row_errors.append(CSVRowError(row_number=idx, artifact_id=art_id, field="category", message="category exceeds maximum length of 100 characters"))
                row_has_error = True
        else:
            row_errors.append(CSVRowError(row_number=idx, artifact_id=art_id, field="category", message="category is required"))
            row_has_error = True

        raw_period = raw_row.get("historical_period")
        historical_period = None
        if raw_period is not None and str(raw_period).strip():
            historical_period = str(unescape_csv_value(str(raw_period).strip()))
            if len(historical_period) > 100:
                row_errors.append(CSVRowError(row_number=idx, artifact_id=art_id, field="historical_period", message="historical_period exceeds maximum length of 100 characters"))
                row_has_error = True
        else:
            row_errors.append(CSVRowError(row_number=idx, artifact_id=art_id, field="historical_period", message="historical_period is required"))
            row_has_error = True

        raw_culture = raw_row.get("origin_culture")
        origin_culture = None
        if raw_culture is not None and str(raw_culture).strip():
            origin_culture = str(unescape_csv_value(str(raw_culture).strip()))
            if len(origin_culture) > 100:
                row_errors.append(CSVRowError(row_number=idx, artifact_id=art_id, field="origin_culture", message="origin_culture exceeds maximum length of 100 characters"))
                row_has_error = True
        else:
            row_errors.append(CSVRowError(row_number=idx, artifact_id=art_id, field="origin_culture", message="origin_culture is required"))
            row_has_error = True

        raw_loc = raw_row.get("origin_location")
        origin_location = None
        if raw_loc is not None and str(raw_loc).strip():
            origin_location = str(unescape_csv_value(str(raw_loc).strip()))
            if len(origin_location) > 256:
                row_errors.append(CSVRowError(row_number=idx, artifact_id=art_id, field="origin_location", message="origin_location exceeds maximum length of 256 characters"))
                row_has_error = True
        else:
            row_errors.append(CSVRowError(row_number=idx, artifact_id=art_id, field="origin_location", message="origin_location is required"))
            row_has_error = True

        year_earliest = None
        year_latest = None
        try:
            year_earliest = parse_csv_int(raw_row.get("creation_year_earliest"), "creation_year_earliest")
        except ValueError as ex:
            row_errors.append(CSVRowError(row_number=idx, artifact_id=art_id, field="creation_year_earliest", message=str(ex)))
            row_has_error = True

        try:
            year_latest = parse_csv_int(raw_row.get("creation_year_latest"), "creation_year_latest")
        except ValueError as ex:
            row_errors.append(CSVRowError(row_number=idx, artifact_id=art_id, field="creation_year_latest", message=str(ex)))
            row_has_error = True

        if year_earliest is not None and year_latest is not None and year_earliest > year_latest:
            row_errors.append(CSVRowError(
                row_number=idx,
                artifact_id=art_id,
                field="creation_year_earliest",
                message="creation_year_earliest cannot be greater than creation_year_latest",
            ))
            row_has_error = True

        medium_materials = parse_csv_list(raw_row.get("medium_materials"))
        if not medium_materials:
            row_errors.append(CSVRowError(row_number=idx, artifact_id=art_id, field="medium_materials", message="medium_materials must contain at least one material"))
            row_has_error = True

        dim_h, dim_w, dim_d, dim_wt = None, None, None, None
        for dim_f in ("dimensions_height_cm", "dimensions_width_cm", "dimensions_depth_cm", "dimensions_weight_kg"):
            val = raw_row.get(dim_f)
            try:
                parsed_dim = parse_csv_float(val, dim_f, required=False)
                if dim_f == "dimensions_height_cm":
                    dim_h = parsed_dim
                elif dim_f == "dimensions_width_cm":
                    dim_w = parsed_dim
                elif dim_f == "dimensions_depth_cm":
                    dim_d = parsed_dim
                elif dim_f == "dimensions_weight_kg":
                    dim_wt = parsed_dim
            except ValueError as ex:
                row_errors.append(CSVRowError(row_number=idx, artifact_id=art_id, field=dim_f, message=str(ex)))
                row_has_error = True

        dimensions = Dimensions(height_cm=dim_h, width_cm=dim_w, depth_cm=dim_d, weight_kg=dim_wt)

        raw_cond = raw_row.get("condition")
        condition = None
        if raw_cond is not None and str(raw_cond).strip():
            condition = str(unescape_csv_value(str(raw_cond).strip()))
            if len(condition) > 50:
                row_errors.append(CSVRowError(row_number=idx, artifact_id=art_id, field="condition", message="condition exceeds maximum length of 50 characters"))
                row_has_error = True
        else:
            row_errors.append(CSVRowError(row_number=idx, artifact_id=art_id, field="condition", message="condition is required"))
            row_has_error = True

        is_on_display = True
        try:
            is_on_display = parse_csv_boolean(raw_row.get("is_on_display"))
        except ValueError as ex:
            row_errors.append(CSVRowError(row_number=idx, artifact_id=art_id, field="is_on_display", message=str(ex)))
            row_has_error = True

        raw_storage = raw_row.get("storage_location")
        storage_location = None
        if raw_storage is not None and str(raw_storage).strip():
            storage_location = str(unescape_csv_value(str(raw_storage).strip()))
            if len(storage_location) > 256:
                row_errors.append(CSVRowError(row_number=idx, artifact_id=art_id, field="storage_location", message="storage_location exceeds maximum length of 256 characters"))
                row_has_error = True
        else:
            row_errors.append(CSVRowError(row_number=idx, artifact_id=art_id, field="storage_location", message="storage_location is required"))
            row_has_error = True

        estimated_value_usd = None
        try:
            estimated_value_usd = parse_csv_float(raw_row.get("estimated_value_usd"), "estimated_value_usd", required=True)
        except ValueError as ex:
            row_errors.append(CSVRowError(row_number=idx, artifact_id=art_id, field="estimated_value_usd", message=str(ex)))
            row_has_error = True

        raw_acq_date = raw_row.get("acquisition_date")
        acquisition_date = None
        if raw_acq_date is not None and str(raw_acq_date).strip():
            clean_date = str(unescape_csv_value(str(raw_acq_date).strip()))
            try:
                datetime.strptime(clean_date, "%Y-%m-%d")
                acquisition_date = clean_date
            except ValueError:
                row_errors.append(CSVRowError(row_number=idx, artifact_id=art_id, field="acquisition_date", message="acquisition_date must be formatted as YYYY-MM-DD"))
                row_has_error = True
        else:
            row_errors.append(CSVRowError(row_number=idx, artifact_id=art_id, field="acquisition_date", message="acquisition_date is required"))
            row_has_error = True

        raw_acq_method = raw_row.get("acquisition_method")
        acquisition_method = None
        if raw_acq_method is not None and str(raw_acq_method).strip():
            acquisition_method = str(unescape_csv_value(str(raw_acq_method).strip()))
            if len(acquisition_method) > 100:
                row_errors.append(CSVRowError(row_number=idx, artifact_id=art_id, field="acquisition_method", message="acquisition_method exceeds maximum length of 100 characters"))
                row_has_error = True
        else:
            row_errors.append(CSVRowError(row_number=idx, artifact_id=art_id, field="acquisition_method", message="acquisition_method is required"))
            row_has_error = True

        raw_desc = raw_row.get("description")
        description = None
        if raw_desc is not None and str(raw_desc).strip():
            description = str(unescape_csv_value(str(raw_desc).strip()))
        else:
            row_errors.append(CSVRowError(row_number=idx, artifact_id=art_id, field="description", message="description is required"))
            row_has_error = True

        raw_context = raw_row.get("historical_context")
        historical_context = None
        if raw_context is not None and str(raw_context).strip():
            historical_context = str(unescape_csv_value(str(raw_context).strip()))
        else:
            row_errors.append(CSVRowError(row_number=idx, artifact_id=art_id, field="historical_context", message="historical_context is required"))
            row_has_error = True

        raw_prov = raw_row.get("provenance")
        provenance = None
        if raw_prov is not None and str(raw_prov).strip():
            provenance = str(unescape_csv_value(str(raw_prov).strip()))
        else:
            row_errors.append(CSVRowError(row_number=idx, artifact_id=art_id, field="provenance", message="provenance is required"))
            row_has_error = True

        raw_notes = raw_row.get("restoration_notes")
        restoration_notes = None
        if raw_notes is not None and str(raw_notes).strip():
            restoration_notes = str(unescape_csv_value(str(raw_notes).strip()))
        else:
            row_errors.append(CSVRowError(row_number=idx, artifact_id=art_id, field="restoration_notes", message="restoration_notes is required"))
            row_has_error = True

        tags = parse_csv_list(raw_row.get("tags"))

        created_at = None
        raw_created_at = raw_row.get("created_at")
        if raw_created_at and str(raw_created_at).strip():
            clean_created_at = str(unescape_csv_value(str(raw_created_at).strip()))
            try:
                datetime.fromisoformat(clean_created_at.replace("Z", "+00:00"))
                created_at = clean_created_at
            except ValueError:
                pass

        if not row_has_error:
            record_dict = {
                "artifact_id": art_id,
                "accession_number": acc_num,
                "name": name,
                "alternate_names": alt_names,
                "category": category,
                "historical_period": historical_period,
                "origin_culture": origin_culture,
                "origin_location": origin_location,
                "creation_year_earliest": year_earliest,
                "creation_year_latest": year_latest,
                "medium_materials": medium_materials,
                "dimensions": dimensions,
                "condition": condition,
                "is_on_display": is_on_display,
                "storage_location": storage_location,
                "estimated_value_usd": estimated_value_usd,
                "acquisition_date": acquisition_date,
                "acquisition_method": acquisition_method,
                "description": description,
                "historical_context": historical_context,
                "provenance": provenance,
                "restoration_notes": restoration_notes,
                "tags": tags,
            }

            try:
                artifact_model = ArtifactCreate(**record_dict)
                valid_doc = artifact_model.model_dump()
                if created_at:
                    valid_doc["created_at"] = created_at
                parsed_records.append((idx, valid_doc))
            except ValidationError as ve:
                for v_err in ve.errors():
                    field_name = ".".join(str(loc) for loc in v_err["loc"])
                    row_errors.append(CSVRowError(
                        row_number=idx,
                        artifact_id=art_id,
                        field=field_name,
                        message=v_err["msg"],
                    ))

    candidate_ids = list(seen_file_ids.keys())
    existing_collection_ids: List[str] = []
    if candidate_ids:
        existing_set = check_existing_artifact_ids(client, candidate_ids)
        existing_collection_ids = sorted(list(existing_set))
        if existing_set:
            for idx, raw_row in enumerate(all_raw_rows, start=2):
                r_id = raw_row.get("artifact_id")
                if r_id:
                    clean_id = str(unescape_csv_value(str(r_id).strip()))
                    if clean_id in existing_set:
                        row_errors.append(CSVRowError(
                            row_number=idx,
                            artifact_id=clean_id,
                            field="artifact_id",
                            message=f"Artifact with ID '{clean_id}' already exists in the archival collection.",
                        ))

    duplicate_file_ids = sorted(list(duplicate_file_ids_set))
    error_row_numbers = {err.row_number for err in row_errors if err.row_number >= 2}
    error_rows_count = len(error_row_numbers)
    if any(err.row_number == 1 for err in row_errors):
        error_rows_count = total_rows
    valid_records = [doc for (r_num, doc) in parsed_records if r_num not in error_row_numbers]

    row_errors.sort(key=lambda e: (e.row_number, e.field or ""))

    return (
        valid_records,
        row_errors,
        duplicate_file_ids,
        existing_collection_ids,
        total_rows,
        error_rows_count,
    )


def execute_csv_import(
    client: Elasticsearch,
    file_bytes: bytes,
) -> Tuple[bool, int, int, List[str], str, List[CSVRowError]]:
    """Revalidates and executes bulk import of artifact records into Elasticsearch.

    Enforces strict create-only semantics (_op_type="create") to prevent overwriting.
    """
    valid_records, errors, dup_file, dup_es, total_rows, error_rows = validate_csv_import(client, file_bytes)
    if errors or not valid_records:
        msg = "CSV validation failed. Please correct all errors before confirming import."
        if not errors and total_rows == 0:
            msg = "The uploaded CSV file contains no records to import."
        return False, 0, total_rows, [], msg, errors

    now_iso = datetime.now(timezone.utc).isoformat()
    actions = []
    for rec in valid_records:
        doc = dict(rec)
        doc["created_at"] = doc.get("created_at") or now_iso
        doc["updated_at"] = now_iso
        actions.append({
            "_op_type": "create",
            "_index": settings.ELASTICSEARCH_INDEX,
            "_id": doc["artifact_id"],
            "_source": doc,
        })

    try:
        success_count, failed_items = helpers.bulk(
            client,
            actions,
            refresh=True,
            raise_on_error=False,
        )
        imported_ids = [rec["artifact_id"] for rec in valid_records[:success_count]]
        if failed_items:
            import_errors: List[CSVRowError] = []
            for item in failed_items:
                op_data = item.get("create", {})
                failed_id = op_data.get("_id")
                status_code = op_data.get("status")
                err_detail = op_data.get("error", {})
                reason = err_detail.get("reason", "Bulk indexing failure") if isinstance(err_detail, dict) else str(err_detail)
                msg = f"Indexing conflict: {reason}" if status_code == 409 else f"Failed to index: {reason}"
                import_errors.append(CSVRowError(row_number=0, artifact_id=failed_id, field="artifact_id", message=msg))
            return False, success_count, total_rows, imported_ids, "Partial import completed with indexing conflicts.", import_errors

        msg = f"Successfully imported {success_count} artifact records into the archival registry."
        return True, success_count, total_rows, imported_ids, msg, []
    except Exception as ex:
        raise ElasticsearchServiceError(f"Error executing bulk import into Elasticsearch: {ex}")
