"""Elasticsearch Index Initialization and Data Seeding Utility.

This administrative utility creates the museum_artifacts index using the
explicit mapping definition (if the index does not yet exist) and bulk-indexes
the curated sample artifact records using deterministic document IDs.

Safe and idempotent to rerun:
- Preserves existing indices and records without destructive recreation.
- Employs Bulk API 'create' operations so existing document IDs are skipped.
"""

import base64
import copy
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class ElasticsearchAdminClient:
    """Lightweight REST client for Elasticsearch administrative operations."""

    def __init__(
        self,
        base_url: str,
        username: Optional[str] = None,
        password: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        if api_key and api_key.strip():
            self._auth_header = f"ApiKey {api_key.strip()}"
        else:
            auth_bytes = f"{username or ''}:{password or ''}".encode("utf-8")
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
                raw = response.read().decode("utf-8")
                if not raw:
                    return status, {}
                try:
                    payload = json.loads(raw)
                except Exception:
                    payload = {"raw_response": raw}
                return status, payload
        except urllib.error.HTTPError as err:
            status = err.code
            err_body = err.read().decode("utf-8")
            try:
                payload = json.loads(err_body)
            except Exception:
                payload = {"raw_error": err_body}
            return status, payload
        except urllib.error.URLError as err:
            return 0, {"error": str(err.reason)}
        except Exception as err:
            return 0, {"error": str(err)}

    def ping(self) -> bool:
        """Verifies connectivity to Elasticsearch via root endpoint (GET /).

        Supported across Elasticsearch Serverless, Elastic Cloud, and self-managed clusters.
        """
        try:
            status, _ = self.request("GET", "/")
            return status == 200
        except Exception:
            return False


def load_environment_config(env_path: Optional[Path] = None) -> Dict[str, str]:
    """Safely loads key-value pairs from .env and overlays environment variables.

    If .env is missing, falls back cleanly to process environment variables.
    """
    config: Dict[str, str] = {}
    if env_path and env_path.exists():
        with open(env_path, "r", encoding="utf-8") as file:
            for line in file:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    config[key.strip()] = val.strip().strip('"').strip("'")

    for key, val in os.environ.items():
        config[key] = val
    return config


def check_connectivity(client: ElasticsearchAdminClient) -> bool:
    """Verifies that the target Elasticsearch instance is reachable and responsive.

    Uses root endpoint GET / to support Elastic Cloud Serverless (where /_cluster/health
    returns HTTP 410) as well as self-managed and local development clusters.
    """
    status, payload = client.request("GET", "/")
    if status != 200:
        error_msg = payload.get("error", payload.get("raw_error", payload))
        print(f"[FAIL] Elasticsearch connectivity check failed with HTTP {status}: {error_msg}")
        return False

    cluster_name = payload.get("cluster_name", "elasticsearch")
    version_info = payload.get("version", {}).get("number", "serverless")
    print(f"[OK] Elasticsearch is reachable and responsive (cluster: '{cluster_name}', version: {version_info}).")
    return True


# Backward-compatible alias for existing callers
check_cluster_readiness = check_connectivity


def is_serverless_shard_replica_rejection(status: int, payload: Dict[str, Any]) -> bool:
    """Detects whether index creation failed specifically because Serverless rejects shard/replica settings."""
    if status != 400:
        return False

    error_text = json.dumps(payload).lower()
    has_serverless = "serverless" in error_text
    has_shard_or_replica = "number_of_shards" in error_text or "number_of_replicas" in error_text
    return has_serverless and has_shard_or_replica


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

    # Serverless compatibility retry: if Serverless rejects number_of_shards/number_of_replicas
    if is_serverless_shard_replica_rejection(put_status, put_payload):
        print(
            f"[INFO] Serverless mode detected: removing unsupported shard/replica settings and retrying creation..."
        )
        serverless_mapping = copy.deepcopy(mapping_def)
        if "settings" in serverless_mapping and isinstance(serverless_mapping["settings"], dict):
            serverless_mapping["settings"].pop("number_of_shards", None)
            serverless_mapping["settings"].pop("number_of_replicas", None)
            if "index" in serverless_mapping["settings"] and isinstance(serverless_mapping["settings"]["index"], dict):
                serverless_mapping["settings"]["index"].pop("number_of_shards", None)
                serverless_mapping["settings"]["index"].pop("number_of_replicas", None)

        retry_status, retry_payload = client.request("PUT", f"/{index_name}", body=serverless_mapping)
        if retry_status in (200, 201) and retry_payload.get("acknowledged"):
            print(f"[OK] Successfully created index '{index_name}' in Serverless mode with mappings and analyzers.")
            return True
        else:
            print(f"[FAIL] Serverless index creation retry failed (HTTP {retry_status}): {retry_payload}")
            return False

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

    # Attempt an explicit refresh if permitted, but do not require it
    refresh_status, _ = client.request("POST", f"/{index_name}/_refresh")
    if refresh_status not in (200, 201):
        print(f"[INFO] Explicit refresh returned HTTP {refresh_status}; relying on periodic refresh polling.")

    return created_count, skipped_count, failed_count


def poll_for_document_count(
    client: ElasticsearchAdminClient,
    index_name: str,
    expected_count: int,
    timeout_seconds: float = 15.0,
    poll_interval: float = 1.0,
) -> int:
    """Polls the count endpoint until at least expected_count documents are searchable or timeout expires.

    Enables least-privileged API keys to initialize successfully without requiring explicit
    refresh privileges in Elastic Cloud Serverless.
    """
    deadline = time.time() + timeout_seconds
    latest_count = -1

    while True:
        status, payload = client.request("GET", f"/{index_name}/_count")
        if status == 200:
            latest_count = payload.get("count", 0)
            if latest_count >= expected_count:
                return latest_count

        remaining = deadline - time.time()
        if remaining <= 0:
            break

        time.sleep(min(poll_interval, remaining))

    return latest_count


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

    if config.get("ELASTICSEARCH_URL"):
        base_url = config["ELASTICSEARCH_URL"].rstrip("/")
    else:
        host = config.get("ELASTICSEARCH_HOST", "127.0.0.1")
        port = config.get("ELASTICSEARCH_PORT", "9200")
        scheme = config.get("ELASTICSEARCH_SCHEME", "http")
        base_url = f"{scheme}://{host}:{port}"

    api_key = config.get("ELASTIC_API_KEY")
    user = config.get("ELASTIC_USER", "elastic")
    password = config.get("ELASTIC_PASSWORD", "")
    index_name = config.get("ELASTICSEARCH_INDEX", "museum_artifacts")

    if api_key and api_key.strip():
        print(f"[INFO] Connecting to {base_url} using API key authentication (Target index: '{index_name}')...")
        client = ElasticsearchAdminClient(base_url, api_key=api_key.strip())
    else:
        print(f"[INFO] Connecting to {base_url} as user '{user}' (Target index: '{index_name}')...")
        client = ElasticsearchAdminClient(base_url, username=user, password=password)

    # 1. Connectivity Check
    if not check_connectivity(client):
        return 1

    # 2. Ensure Index Exists
    if not ensure_index_exists(client, index_name, mapping_file):
        return 1

    # 3. Seed Sample Data
    created, skipped, failed = seed_sample_data(client, index_name, sample_data_file)
    print("[SUMMARY] Bulk indexing results:")
    print(f"  - Newly indexed:  {created}")
    print(f"  - Already exists: {skipped}")
    print(f"  - Failures:       {failed}")

    if failed > 0:
        print("[FAIL] One or more documents failed to index.")
        return 1

    # Determine expected minimum documents based on sample data
    with open(sample_data_file, "r", encoding="utf-8") as f:
        sample_records: List[Dict[str, Any]] = json.load(f)
    expected_count = len(sample_records)

    # 4. Total Document Count Verification with Bounded Polling
    print(f"[INFO] Verifying searchable document count (expected at least {expected_count})...")
    total_docs = poll_for_document_count(
        client,
        index_name,
        expected_count=expected_count,
        timeout_seconds=15.0,
    )

    if total_docs < expected_count:
        print(
            f"[FAIL] Document count verification timed out: found {total_docs} searchable documents, "
            f"expected at least {expected_count}."
        )
        return 1

    # 5. Spot-check sample record accessibility
    if sample_records:
        first_id = sample_records[0].get("artifact_id", "art-001")
        doc_status, _ = client.request("GET", f"/{index_name}/_doc/{first_id}")
        if doc_status != 200:
            print(f"[FAIL] Spot-check verification failed for document '{first_id}' (HTTP {doc_status}).")
            return 1
        print(f"[OK] Spot-checked document '{first_id}' confirmed accessible.")

    print(f"[OK] Total active documents in '{index_name}': {total_docs}")

    print("=" * 70)
    print("Initialization completed successfully.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
