"""Tests for API health and root metadata endpoints."""

from fastapi.testclient import TestClient


def test_root_endpoint(client: TestClient) -> None:
    """Verifies that the root endpoint returns API information and documentation links."""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "name" in data
    assert data["docs_url"] == "/docs"
    assert data["health_url"] == "/api/health"


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
