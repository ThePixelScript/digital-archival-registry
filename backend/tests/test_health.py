"""Tests for API health, root endpoint redirect, and Elasticsearch settings and authentication."""

import base64
import importlib.util
from pathlib import Path
from typing import Any, Dict
from fastapi.testclient import TestClient
from pytest import MonkeyPatch
from app.core.config import Settings, settings
from app.core.elasticsearch import close_elasticsearch_client, get_elasticsearch_client

_INIT_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "initialize_elasticsearch.py"
_spec = importlib.util.spec_from_file_location("initialize_elasticsearch", _INIT_SCRIPT)
_init_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_init_mod)
load_environment_config = _init_mod.load_environment_config
ElasticsearchAdminClient = _init_mod.ElasticsearchAdminClient


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
