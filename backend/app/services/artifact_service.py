"""Elasticsearch service module encapsulating artifact query generation, search, and CRUD operations."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from elasticsearch import Elasticsearch, ApiError, ConflictError, NotFoundError
from app.core.config import settings


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

    # Exact-match keyword filters
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

    # Sort configuration
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
    """Gathers cluster health and active index metadata without exposing sensitive credentials."""
    try:
        health = client.cluster.health()
        count_res = client.count(index=settings.ELASTICSEARCH_INDEX)
        doc_count = count_res.get("count", 0)

        return {
            "connected": True,
            "cluster_name": health.get("cluster_name"),
            "cluster_status": health.get("status"),
            "number_of_nodes": health.get("number_of_nodes"),
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
