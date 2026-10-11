"""Tests for collection statistics and aggregation endpoints."""

from fastapi.testclient import TestClient


def test_collection_stats_endpoint(client: TestClient) -> None:
    """Verifies that /api/stats returns aggregation counts and valuation metrics."""
    response = client.get("/api/stats")
    assert response.status_code == 200
    data = response.json()

    assert data["total_artifacts"] >= 30
    assert len(data["categories"]) >= 6
    assert "Sculpture" in data["categories"]
    assert "Ceramics & Pottery" in data["categories"]

    assert len(data["historical_periods"]) >= 8
    assert "Classical Antiquity" in data["historical_periods"]

    assert "Pristine" in data["conditions"]
    assert "Good" in data["conditions"]

    val = data["valuation"]
    assert val["count"] >= 30
    assert val["min"] > 0
    assert val["max"] >= 10000000
    assert val["avg"] > 0
    assert val["sum"] > 0
