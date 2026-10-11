"""Elasticsearch Index Initialization and Data Seeding Utility.

This administrative utility creates the museum_artifacts index using the
explicit mapping definition (if the index does not yet exist) and bulk-indexes
the curated sample artifact records using deterministic document IDs.

Safe and idempotent to rerun:
- Preserves existing indices and records without destructive recreation.
- Employs Bulk API 'create' operations so existing document IDs are skipped.
"""

import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class ElasticsearchAdminClient:
    """Lightweight REST client for Elasticsearch administrative operations."""

    def __init__(self, base_url: str, username: str, password: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        auth_bytes = f"{username}:{password}".encode("utf-8")
        self._auth_header = f"Basic {base64.b64encode(auth_bytes).decode('ascii')}"

    def request(
        self,
        method: str,
        path: str,
        body: Optional[Any] = None,
        content_type: str = "application/json"
    ) -> Tuple[int, Dict[str, Any]]:
        """Executes an authenticated HTTP request to Elasticsearch."""
        url = f"{self.base_url}/{path.lstrip('/')}"
        data = None
        if body is not None:
            if isinstance(body, (dict, list)):
                data = json.dumps(body).encode("utf-8")
            elif isinstance(body, str):
                data = body.encode("utf-8")
            elif isinstance(body, bytes):
                data = body
            else:
                data = str(body).encode("utf-8")

        req = urllib.request.Request(url, data=data, method=method.upper())
        req.add_header("Authorization", self._auth_header)
        req.add_header("Content-Type", content_type)
        req.add_header("Accept", "application/json")

        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                status = response.status
                payload = json.loads(response.read().decode("utf-8"))
                return status, payload
        except urllib.error.HTTPError as err:
            status = err.code
            err_body = err.read().decode("utf-8")
            try:
                payload = json.loads(err_body)
            except Exception:
                payload = {"raw_error": err_body}
            return status, payload


def load_environment_config(env_path: Path) -> Dict[str, str]:
    """Safely loads key-value pairs from .env without shell interpolation or leaks."""
    if not env_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {env_path}")

    config = {}
    with open(env_path, "r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                config[key.strip()] = val.strip().strip('"').strip("'")
    return config


def check_cluster_readiness(client: ElasticsearchAdminClient) -> bool:
    """Verifies that the target Elasticsearch cluster is reachable and healthy."""
    status, payload = client.request("GET", "/_cluster/health")
    if status != 200:
        print(f"[FAIL] Cluster health request failed with HTTP {status}: {payload}")
        return False

    cluster_status = payload.get("status")
    cluster_name = payload.get("cluster_name")
    nodes = payload.get("number_of_nodes")
    print(f"[OK] Cluster '{cluster_name}' is reachable. Health: {cluster_status} (nodes: {nodes})")
    return True


def ensure_index_exists(
    client: ElasticsearchAdminClient,
    index_name: str,
    mapping_path: Path
) -> bool:
    """Creates the index with explicit settings and mappings if missing, preserving existing index."""
    # Check if index already exists
    status, payload = client.request("GET", f"/{index_name}")
    if status == 200:
        print(f"[INFO] Index '{index_name}' already exists. Preserving existing index schema.")
        return True
    elif status != 404:
        print(f"[FAIL] Unexpected error checking index '{index_name}' (HTTP {status}): {payload}")
        return False

    # Index does not exist: create with explicit configuration
    print(f"[INFO] Index '{index_name}' not found. Creating from {mapping_path.name}...")
    with open(mapping_path, "r", encoding="utf-8") as f:
        mapping_def = json.load(f)

    put_status, put_payload = client.request("PUT", f"/{index_name}", body=mapping_def)
    if put_status in (200, 201) and put_payload.get("acknowledged"):
        print(f"[OK] Successfully created index '{index_name}' with explicit mappings and analyzers.")
        return True
    else:
        print(f"[FAIL] Failed to create index '{index_name}' (HTTP {put_status}): {put_payload}")
        return False


def seed_sample_data(
    client: ElasticsearchAdminClient,
    index_name: str,
    data_path: Path
) -> Tuple[int, int, int]:
    """Bulk-seeds sample documents into the index using deterministic IDs.

    Returns:
        Tuple of (created_count, skipped_count, failed_count).
    """
    if not data_path.exists():
        raise FileNotFoundError(f"Sample data file not found: {data_path}")

    with open(data_path, "r", encoding="utf-8") as f:
        artifacts: List[Dict[str, Any]] = json.load(f)

    if not artifacts:
        print(f"[WARN] No records found in {data_path.name}.")
        return 0, 0, 0

    print(f"[INFO] Preparing bulk indexing for {len(artifacts)} artifacts...")

    # Build NDJSON payload with 'create' action to enforce idempotency
    ndjson_lines = []
    for art in artifacts:
        art_id = art["artifact_id"]
        action = {"create": {"_index": index_name, "_id": art_id}}
        ndjson_lines.append(json.dumps(action, ensure_ascii=False))
        ndjson_lines.append(json.dumps(art, ensure_ascii=False))

    bulk_body = "\n".join(ndjson_lines) + "\n"
    status, payload = client.request(
        "POST",
        "/_bulk",
        body=bulk_body,
        content_type="application/x-ndjson"
    )

    if status not in (200, 201):
        print(f"[FAIL] Bulk request failed with HTTP {status}: {payload}")
        return 0, 0, len(artifacts)

    created_count = 0
    skipped_count = 0
    failed_count = 0

    for item in payload.get("items", []):
        create_res = item.get("create", {})
        doc_id = create_res.get("_id")
        res_status = create_res.get("status")

        if res_status == 201:
            created_count += 1
        elif res_status == 409:
            # 409 Conflict indicates the document ID already exists
            skipped_count += 1
        else:
            failed_count += 1
            error_info = create_res.get("error", {})
            reason = error_info.get("reason", "Unknown reason")
            print(f"  [ERROR] Document {doc_id} failed (status {res_status}): {reason}")

    # Explicitly refresh the index to make new documents immediately searchable
    client.request("POST", f"/{index_name}/_refresh")

    return created_count, skipped_count, failed_count


def verify_total_count(client: ElasticsearchAdminClient, index_name: str) -> int:
    """Queries the index count endpoint to confirm total indexed documents."""
    status, payload = client.request("GET", f"/{index_name}/_count")
    if status == 200:
        return payload.get("count", 0)
    return -1


def main() -> int:
    # Resolve repository directories
    project_root = Path(__file__).resolve().parent.parent
    env_file = project_root / ".env"
    mapping_file = project_root / "elasticsearch" / "mappings" / "artifacts_mapping.json"
    sample_data_file = project_root / "elasticsearch" / "sample_data" / "sample_artifacts.json"

    print("=" * 70)
    print("Digital Archival Registry - Elasticsearch Initialization Utility")
    print("=" * 70)

    try:
        config = load_environment_config(env_file)
    except Exception as ex:
        print(f"[FAIL] Error reading environment configuration: {ex}")
        return 1

    host = config.get("ELASTICSEARCH_HOST", "127.0.0.1")
    port = config.get("ELASTICSEARCH_PORT", "9200")
    scheme = config.get("ELASTICSEARCH_SCHEME", "http")
    user = config.get("ELASTIC_USER", "elastic")
    password = config.get("ELASTIC_PASSWORD", "")
    index_name = config.get("ELASTICSEARCH_INDEX", "museum_artifacts")

    base_url = f"{scheme}://{host}:{port}"
    print(f"[INFO] Connecting to {base_url} as user '{user}' (Target index: '{index_name}')...")

    client = ElasticsearchAdminClient(base_url, user, password)

    # 1. Cluster Readiness
    if not check_cluster_readiness(client):
        return 1

    # 2. Ensure Index Exists
    if not ensure_index_exists(client, index_name, mapping_file):
        return 1

    # 3. Seed Sample Data
    created, skipped, failed = seed_sample_data(client, index_name, sample_data_file)
    print(f"[SUMMARY] Bulk indexing results:")
    print(f"  - Newly indexed:  {created}")
    print(f"  - Already exists: {skipped}")
    print(f"  - Failures:       {failed}")

    if failed > 0:
        print(f"[FAIL] One or more documents failed to index.")
        return 1

    # 4. Total Document Count Verification
    total_docs = verify_total_count(client, index_name)
    print(f"[OK] Total active documents in '{index_name}': {total_docs}")

    print("=" * 70)
    print("Initialization completed successfully.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
