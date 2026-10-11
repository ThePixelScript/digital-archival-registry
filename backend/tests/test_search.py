"""Tests for full-text search, fuzzy matching, filtering, and sorting."""

from fastapi.testclient import TestClient


def test_full_text_search_relevance(client: TestClient) -> None:
    """Verifies that full-text queries rank relevant documents highly with scores."""
    response = client.get("/api/search?q=cuneiform+sumerian+cylinder")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] > 0
    assert data["took_ms"] >= 0
    top_hit = data["items"][0]
    assert top_hit["artifact_id"] == "art-001"
    assert top_hit["relevance_score"] is not None
    assert top_hit["relevance_score"] > 0


def test_fuzzy_search_typo_tolerance(client: TestClient) -> None:
    """Verifies that enabling fuzzy search retrieves artifacts despite typographical errors."""
    # Search for 'astrolbae' (typo of 'astrolabe') with fuzzy=true
    response = client.get("/api/search?q=astrolbae&fuzzy=true")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] > 0
    assert data["fuzzy_applied"] is True
    found_astrolabe = any("astrolabe" in item["name"].lower() for item in data["items"])
    assert found_astrolabe is True


def test_search_with_category_filter(client: TestClient) -> None:
    """Verifies combining search terms with exact category filters."""
    response = client.get("/api/search?q=terracotta&category=Sculpture")
    assert response.status_code == 200
    data = response.json()
    for item in data["items"]:
        assert item["category"] == "Sculpture"


def test_search_sorting_by_value_desc(client: TestClient) -> None:
    """Verifies sorting search results by estimated value descending."""
    response = client.get("/api/search?sort_by=estimated_value_usd&sort_order=desc&page_size=5")
    assert response.status_code == 200
    data = response.json()
    items = data["items"]
    assert len(items) >= 2
    # Check values descend monotonically
    for i in range(len(items) - 1):
        assert items[i]["estimated_value_usd"] >= items[i + 1]["estimated_value_usd"]


def test_search_sorting_by_value_asc(client: TestClient) -> None:
    """Verifies sorting search results by estimated value ascending."""
    response = client.get("/api/search?sort_by=estimated_value_usd&sort_order=asc&page_size=5")
    assert response.status_code == 200
    data = response.json()
    items = data["items"]
    assert len(items) >= 2
    for i in range(len(items) - 1):
        assert items[i]["estimated_value_usd"] <= items[i + 1]["estimated_value_usd"]


def test_empty_search_query(client: TestClient) -> None:
    """Verifies that an empty query returns the complete collection paginated."""
    response = client.get("/api/search?page=1&page_size=10")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 30
    assert len(data["items"]) == 10
