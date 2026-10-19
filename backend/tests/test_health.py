"""Tests for API health, root endpoint redirect, and Elasticsearch settings and authentication."""

import base64
import importlib.util
import json
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock
from fastapi.testclient import TestClient
from pytest import MonkeyPatch
from app.core.config import Settings, settings
from app.core.elasticsearch import close_elasticsearch_client, get_elasticsearch_client
from app.services.artifact_service import get_health_status

_INIT_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "initialize_elasticsearch.py"
_spec = importlib.util.spec_from_file_location("initialize_elasticsearch", _INIT_SCRIPT)
_init_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_init_mod)
load_environment_config = _init_mod.load_environment_config
ElasticsearchAdminClient = _init_mod.ElasticsearchAdminClient
check_connectivity = _init_mod.check_connectivity
ensure_index_exists = _init_mod.ensure_index_exists
is_serverless_shard_replica_rejection = _init_mod.is_serverless_shard_replica_rejection
poll_for_document_count = _init_mod.poll_for_document_count
seed_sample_data = _init_mod.seed_sample_data


def test_root_endpoint(client: TestClient) -> None:
    """Verifies that the root endpoint redirects to the frontend application at /app/."""
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers.get("location") == "/app/"


def test_health_endpoint(client: TestClient) -> None:
    """Verifies that the /api/health endpoint reports service status and Elasticsearch connectivity."""
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["environment"] == "development"
    assert "elasticsearch" in data
    es_info = data["elasticsearch"]
    assert es_info["connected"] is True
    assert es_info["cluster_status"] in ("green", "yellow")
    assert es_info["document_count"] >= 30


def test_elasticsearch_url_configuration_precedence() -> None:
    """Verifies that ELASTICSEARCH_URL overrides host, port, and scheme when provided."""
    default_settings = Settings(
        ELASTICSEARCH_HOST="127.0.0.1",
        ELASTICSEARCH_PORT=9200,
        ELASTICSEARCH_SCHEME="http",
        ELASTICSEARCH_URL=None,
    )
    assert default_settings.elasticsearch_url == "http://127.0.0.1:9200"

    custom_url = "https://custom-cluster.es.io:9243"
    override_settings = Settings(
        ELASTICSEARCH_HOST="127.0.0.1",
        ELASTICSEARCH_PORT=9200,
        ELASTICSEARCH_SCHEME="http",
        ELASTICSEARCH_URL=custom_url,
    )
    assert override_settings.elasticsearch_url == custom_url

    trailing_slash_settings = Settings(
        ELASTICSEARCH_URL="https://custom-cluster.es.io:9243/",
    )
    assert trailing_slash_settings.elasticsearch_url == "https://custom-cluster.es.io:9243"


def test_elasticsearch_client_api_key_authentication_selection(monkeypatch: MonkeyPatch) -> None:
    """Verifies that get_elasticsearch_client selects api_key when ELASTIC_API_KEY is configured."""
    captured_kwargs: Dict[str, Any] = {}

    class DummyElasticsearch:
        def __init__(self, **kwargs: Any) -> None:
            captured_kwargs.update(kwargs)

        def close(self) -> None:
            pass

    monkeypatch.setattr("app.core.elasticsearch.Elasticsearch", DummyElasticsearch)

    # 1. When ELASTIC_API_KEY is provided
    monkeypatch.setattr(settings, "ELASTIC_API_KEY", "dummy_serverless_api_key")
    close_elasticsearch_client()
    try:
        client = get_elasticsearch_client()
        assert isinstance(client, DummyElasticsearch)
        assert captured_kwargs.get("api_key") == "dummy_serverless_api_key"
        assert "basic_auth" not in captured_kwargs
    finally:
        close_elasticsearch_client()

    # 2. When ELASTIC_API_KEY is None, falls back to basic_auth
    captured_kwargs.clear()
    monkeypatch.setattr(settings, "ELASTIC_API_KEY", None)
    monkeypatch.setattr(settings, "ELASTIC_USER", "dummy_user")
    monkeypatch.setattr(settings, "ELASTIC_PASSWORD", "dummy_pass")
    close_elasticsearch_client()
    try:
        client = get_elasticsearch_client()
        assert isinstance(client, DummyElasticsearch)
        assert captured_kwargs.get("basic_auth") == ("dummy_user", "dummy_pass")
        assert "api_key" not in captured_kwargs
    finally:
        close_elasticsearch_client()


def test_elasticsearch_admin_client_authentication_header_selection() -> None:
    """Verifies that ElasticsearchAdminClient correctly builds ApiKey vs Basic auth headers."""
    # ApiKey authentication
    api_key_client = ElasticsearchAdminClient("http://127.0.0.1:9200", api_key="dummy_encoded_key")
    assert api_key_client._auth_header == "ApiKey dummy_encoded_key"

    # Basic authentication fallback
    basic_client = ElasticsearchAdminClient("http://127.0.0.1:9200", username="dummy_user", password="dummy_password")
    expected_basic = "Basic " + base64.b64encode(b"dummy_user:dummy_password").decode("ascii")
    assert basic_client._auth_header == expected_basic


def test_initialize_elasticsearch_load_environment_config(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Verifies that initialize_elasticsearch loads from environment variables and handles missing .env."""
    non_existent = tmp_path / "non_existent.env"
    monkeypatch.setenv("ELASTICSEARCH_URL", "https://remote-es:9243")
    monkeypatch.setenv("ELASTIC_USER", "remote_user")
    monkeypatch.setenv("ELASTIC_API_KEY", "dummy_env_api_key")
    config = load_environment_config(non_existent)
    assert config.get("ELASTICSEARCH_URL") == "https://remote-es:9243"
    assert config.get("ELASTIC_USER") == "remote_user"
    assert config.get("ELASTIC_API_KEY") == "dummy_env_api_key"


def test_elasticsearch_admin_client_ping(monkeypatch: MonkeyPatch) -> None:
    """Verifies that client.ping() returns True on 200 OK and False on failure or exception."""
    client = ElasticsearchAdminClient("http://127.0.0.1:9200", api_key="dummy_api_key")

    # 1. Successful ping
    monkeypatch.setattr(client, "request", lambda method, path: (200, {"tagline": "You Know, for Search"}))
    assert client.ping() is True

    # 2. HTTP error response
    monkeypatch.setattr(client, "request", lambda method, path: (401, {"error": "unauthorized"}))
    assert client.ping() is False

    # 3. Network or transport error
    monkeypatch.setattr(client, "request", lambda method, path: (0, {"error": "connection refused"}))
    assert client.ping() is False


def test_check_connectivity_success(monkeypatch: MonkeyPatch) -> None:
    """Verifies check_connectivity succeeds with Serverless or standard Elasticsearch root metadata."""
    client = ElasticsearchAdminClient("https://serverless-project.es.io:9243", api_key="dummy_key")
    serverless_response = {
        "name": "serverless-node",
        "cluster_name": "serverless-project",
        "version": {"number": "8.17.0"},
        "tagline": "You Know, for Search",
    }
    monkeypatch.setattr(client, "request", lambda method, path: (200, serverless_response))
    assert check_connectivity(client) is True


def test_check_connectivity_failure_handling(monkeypatch: MonkeyPatch) -> None:
    """Verifies check_connectivity handles HTTP 410, 401, and connection failures safely."""
    client = ElasticsearchAdminClient("https://serverless-project.es.io:9243", api_key="dummy_key")

    # 1. Handles HTTP 410 (the Serverless /_cluster/health error scenario)
    monkeypatch.setattr(
        client,
        "request",
        lambda method, path: (410, {"error": "Endpoint unavailable in Serverless"}),
    )
    assert check_connectivity(client) is False

    # 2. Handles HTTP 401 Unauthorized
    monkeypatch.setattr(
        client,
        "request",
        lambda method, path: (401, {"error": {"root_cause": [{"type": "security_exception"}]}}),
    )
    assert check_connectivity(client) is False

    # 3. Handles transport failure (HTTP 0)
    monkeypatch.setattr(
        client,
        "request",
        lambda method, path: (0, {"error": "Connection refused"}),
    )
    assert check_connectivity(client) is False


def test_is_serverless_shard_replica_rejection() -> None:
    """Verifies precise detection of Serverless shard/replica setting rejection."""
    # 1. Matches exact Serverless rejection message
    payload_exact = {
        "error": {
            "root_cause": [
                {
                    "type": "illegal_argument_exception",
                    "reason": "Settings [index.number_of_replicas,index.number_of_shards] are not available when running in serverless mode",
                }
            ],
            "type": "illegal_argument_exception",
            "reason": "Settings [index.number_of_replicas,index.number_of_shards] are not available when running in serverless mode",
        },
        "status": 400,
    }
    assert is_serverless_shard_replica_rejection(400, payload_exact) is True

    # 2. Matches single shard setting rejection
    payload_shards = {"error": "Settings [index.number_of_shards] are not available when running in serverless mode"}
    assert is_serverless_shard_replica_rejection(400, payload_shards) is True

    # 3. Does not match unrelated 400 error
    payload_mapping = {"error": "Invalid field mapping for description: unknown type"}
    assert is_serverless_shard_replica_rejection(400, payload_mapping) is False

    # 4. Does not match non-400 status codes even if string mentions serverless
    assert is_serverless_shard_replica_rejection(403, {"error": "Forbidden in serverless mode"}) is False
    assert is_serverless_shard_replica_rejection(500, payload_exact) is False


def test_ensure_index_exists_normal_creation(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Verifies that normal index creation succeeds with explicit shard/replica settings on standard clusters."""
    mapping_file = tmp_path / "test_mapping.json"
    mapping_content = {
        "settings": {
            "number_of_shards": 1,
            "number_of_replicas": 0,
            "analysis": {"analyzer": {"custom_analyzer": {"type": "standard"}}},
        },
        "mappings": {"properties": {"artifact_id": {"type": "keyword"}}},
    }
    mapping_file.write_text(json.dumps(mapping_content), encoding="utf-8")

    client = ElasticsearchAdminClient("http://127.0.0.1:9200", username="elastic", password="changeme")
    recorded_requests = []

    def mock_request(method: str, path: str, body: Any = None, content_type: str = "application/json"):
        recorded_requests.append((method, path, body))
        if method == "GET" and path == "/museum_artifacts":
            return (404, {"error": "index_not_found_exception"})
        if method == "PUT" and path == "/museum_artifacts":
            return (200, {"acknowledged": True})
        return (500, {})

    monkeypatch.setattr(client, "request", mock_request)

    result = ensure_index_exists(client, "museum_artifacts", mapping_file)
    assert result is True
    assert len(recorded_requests) == 2
    put_body = recorded_requests[1][2]
    assert put_body["settings"]["number_of_shards"] == 1
    assert put_body["settings"]["number_of_replicas"] == 0
    assert "analysis" in put_body["settings"]


def test_ensure_index_exists_serverless_fallback(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Verifies that Serverless rejection of shards/replicas triggers a single retry with those settings removed."""
    mapping_file = tmp_path / "test_mapping.json"
    mapping_content = {
        "settings": {
            "number_of_shards": 1,
            "number_of_replicas": 0,
            "analysis": {"analyzer": {"custom_analyzer": {"type": "standard"}}},
        },
        "mappings": {"properties": {"artifact_id": {"type": "keyword"}}},
    }
    mapping_file.write_text(json.dumps(mapping_content), encoding="utf-8")

    client = ElasticsearchAdminClient("https://serverless-project.es.io:9243", api_key="dummy_api_key")
    recorded_requests = []

    def mock_request(method: str, path: str, body: Any = None, content_type: str = "application/json"):
        recorded_requests.append((method, path, body))
        if method == "GET" and path == "/museum_artifacts":
            return (404, {"error": "index_not_found_exception"})
        if method == "PUT" and path == "/museum_artifacts":
            if len([r for r in recorded_requests if r[0] == "PUT"]) == 1:
                return (
                    400,
                    {
                        "error": {
                            "reason": "Settings [index.number_of_replicas,index.number_of_shards] are not available when running in serverless mode"
                        }
                    },
                )
            else:
                return (200, {"acknowledged": True})
        return (500, {})

    monkeypatch.setattr(client, "request", mock_request)

    result = ensure_index_exists(client, "museum_artifacts", mapping_file)
    assert result is True

    # Total requests: 1 GET, 2 PUTs
    assert len(recorded_requests) == 3
    first_put_body = recorded_requests[1][2]
    assert "number_of_shards" in first_put_body["settings"]

    second_put_body = recorded_requests[2][2]
    # Rejected settings must be stripped
    assert "number_of_shards" not in second_put_body["settings"]
    assert "number_of_replicas" not in second_put_body["settings"]
    # Analyzers and field mappings must be preserved
    assert "analysis" in second_put_body["settings"]
    assert "custom_analyzer" in second_put_body["settings"]["analysis"]["analyzer"]
    assert second_put_body["mappings"]["properties"]["artifact_id"]["type"] == "keyword"


def test_ensure_index_exists_unrelated_failure_no_retry(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Verifies that unrelated index creation failures do not trigger retry and fail immediately."""
    mapping_file = tmp_path / "test_mapping.json"
    mapping_content = {"settings": {}, "mappings": {}}
    mapping_file.write_text(json.dumps(mapping_content), encoding="utf-8")

    client = ElasticsearchAdminClient("http://127.0.0.1:9200", api_key="dummy_api_key")
    put_count = 0

    def mock_request(method: str, path: str, body: Any = None, content_type: str = "application/json"):
        nonlocal put_count
        if method == "GET":
            return (404, {"error": "not found"})
        if method == "PUT":
            put_count += 1
            return (400, {"error": "Invalid analyzer definition"})
        return (500, {})

    monkeypatch.setattr(client, "request", mock_request)

    result = ensure_index_exists(client, "museum_artifacts", mapping_file)
    assert result is False
    assert put_count == 1


def test_poll_for_document_count_immediate(monkeypatch: MonkeyPatch) -> None:
    """Verifies that poll_for_document_count returns immediately when documents are already indexed."""
    client = ElasticsearchAdminClient("https://serverless-project.es.io:9243", api_key="dummy_key")
    call_count = 0

    def mock_request(method: str, path: str, body: Any = None, content_type: str = "application/json"):
        nonlocal call_count
        call_count += 1
        return (200, {"count": 30})

    monkeypatch.setattr(client, "request", mock_request)

    count = poll_for_document_count(client, "museum_artifacts", expected_count=30, timeout_seconds=1.0)
    assert count == 30
    assert call_count == 1


def test_poll_for_document_count_delayed_success(monkeypatch: MonkeyPatch) -> None:
    """Verifies that poll_for_document_count polls until the expected count is reached before timeout."""
    client = ElasticsearchAdminClient("https://serverless-project.es.io:9243", api_key="dummy_key")
    call_count = 0

    def mock_request(method: str, path: str, body: Any = None, content_type: str = "application/json"):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            return (200, {"count": 0})
        return (200, {"count": 30})

    monkeypatch.setattr(client, "request", mock_request)

    count = poll_for_document_count(
        client,
        "museum_artifacts",
        expected_count=30,
        timeout_seconds=1.0,
        poll_interval=0.01,
    )
    assert count == 30
    assert call_count == 3


def test_poll_for_document_count_timeout(monkeypatch: MonkeyPatch) -> None:
    """Verifies that poll_for_document_count stops polling and returns the latest count upon timeout."""
    client = ElasticsearchAdminClient("https://serverless-project.es.io:9243", api_key="dummy_key")

    def mock_request(method: str, path: str, body: Any = None, content_type: str = "application/json"):
        return (200, {"count": 5})

    monkeypatch.setattr(client, "request", mock_request)

    count = poll_for_document_count(
        client,
        "museum_artifacts",
        expected_count=30,
        timeout_seconds=0.05,
        poll_interval=0.01,
    )
    assert count == 5


def test_seed_sample_data_refresh_forbidden_gracefully_tolerated(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """Verifies that seed_sample_data handles HTTP 403 on _refresh gracefully without failing."""
    sample_file = tmp_path / "sample.json"
    sample_records = [
        {"artifact_id": "art-001", "name": "Relic 1"},
        {"artifact_id": "art-002", "name": "Relic 2"},
    ]
    sample_file.write_text(json.dumps(sample_records), encoding="utf-8")

    client = ElasticsearchAdminClient("https://serverless-project.es.io:9243", api_key="dummy_key")

    def mock_request(method: str, path: str, body: Any = None, content_type: str = "application/json"):
        if method == "POST" and path == "/_bulk":
            return (
                200,
                {
                    "items": [
                        {"create": {"_id": "art-001", "status": 201}},
                        {"create": {"_id": "art-002", "status": 201}},
                    ]
                },
            )
        if method == "POST" and path.endswith("/_refresh"):
            return (403, {"error": {"reason": "action [indices:admin/refresh] is unauthorized"}})
        return (500, {})

    monkeypatch.setattr(client, "request", mock_request)

    created, skipped, failed = seed_sample_data(client, "museum_artifacts", sample_file)
    assert created == 2
    assert skipped == 0
    assert failed == 0


def test_get_health_status_serverless_cluster() -> None:
    """Verifies that get_health_status succeeds and leaves cluster_status/nodes as None on Serverless."""
    mock_client = MagicMock()
    mock_client.info.return_value = {"cluster_name": "serverless_cluster_123"}
    mock_client.count.return_value = {"count": 30}
    mock_client.cluster.health.side_effect = Exception("HTTP 410 Gone: Cluster health API is not supported in serverless")

    result = get_health_status(mock_client)
    assert result["connected"] is True
    assert result["cluster_name"] == "serverless_cluster_123"
    assert result["cluster_status"] is None
    assert result["number_of_nodes"] is None
    assert result["document_count"] == 30
    assert result["index_name"] == settings.ELASTICSEARCH_INDEX


def test_get_health_status_local_cluster() -> None:
    """Verifies that get_health_status populates cluster_status and nodes on standard clusters."""
    mock_client = MagicMock()
    mock_client.info.return_value = {"cluster_name": "docker-cluster"}
    mock_client.count.return_value = {"count": 30}
    mock_client.cluster.health.return_value = {"status": "green", "number_of_nodes": 1}

    result = get_health_status(mock_client)
    assert result["connected"] is True
    assert result["cluster_name"] == "docker-cluster"
    assert result["cluster_status"] == "green"
    assert result["number_of_nodes"] == 1
    assert result["document_count"] == 30


def test_get_health_status_unreachable_cluster() -> None:
    """Verifies that get_health_status reports connected=False when the cluster is completely unreachable."""
    mock_client = MagicMock()
    mock_client.info.side_effect = Exception("Connection refused")

    result = get_health_status(mock_client)
    assert result["connected"] is False
    assert "error" in result
    assert result.get("cluster_name") is None
    assert result.get("cluster_status") is None
    assert result.get("number_of_nodes") is None
    assert result["document_count"] == 0
