"""Tests for artifact CRUD operations and validation rules."""

from fastapi.testclient import TestClient


def test_list_artifacts_pagination(client: TestClient) -> None:
    """Verifies artifact listing with pagination parameters."""
    response = client.get("/api/artifacts?page=1&page_size=5")
    assert response.status_code == 200
    data = response.json()
    assert data["page"] == 1
    assert data["page_size"] == 5
    assert data["total"] >= 30
    assert len(data["items"]) == 5
    assert data["total_pages"] >= 6


def test_list_artifacts_filtered(client: TestClient) -> None:
    """Verifies artifact listing with exact category filter."""
    response = client.get("/api/artifacts?category=Sculpture")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] > 0
    for item in data["items"]:
        assert item["category"] == "Sculpture"


def test_get_artifact_by_id(client: TestClient) -> None:
    """Verifies retrieval of an existing artifact by ID."""
    response = client.get("/api/artifacts/art-001")
    assert response.status_code == 200
    data = response.json()
    assert data["artifact_id"] == "art-001"
    assert data["accession_number"] == "ARC-2024-001"
    assert "Ur-Nammu" in data["name"]
    assert data["category"] == "Manuscripts & Rare Texts"


def test_get_artifact_not_found(client: TestClient) -> None:
    """Verifies that requesting a non-existent artifact returns 404."""
    response = client.get("/api/artifacts/art-nonexistent-999")
    assert response.status_code == 404
    data = response.json()
    assert "not found" in data["detail"].lower()


def test_artifact_crud_lifecycle(client: TestClient) -> None:
    """Verifies end-to-end artifact creation, duplication prevention, update, and deletion."""
    test_id = "art-test-integration-001"
    new_artifact = {
        "artifact_id": test_id,
        "accession_number": "ARC-TEST-999",
        "name": "Integration Test Bronze Votive Dagger",
        "alternate_names": "Synthetic Test Dagger",
        "category": "Metalwork & Arms",
        "historical_period": "Bronze Age",
        "origin_culture": "Minoan",
        "origin_location": "Knossos Sanctuary, Crete",
        "creation_year_earliest": -1500,
        "creation_year_latest": -1400,
        "medium_materials": ["Cast bronze", "Gold foil"],
        "dimensions": {
            "height_cm": 24.5,
            "width_cm": 4.2,
            "depth_cm": 1.1,
            "weight_kg": 0.35,
        },
        "condition": "Excellent",
        "is_on_display": False,
        "storage_location": "Test Storage Locker 1",
        "estimated_value_usd": 125000.0,
        "acquisition_date": "2024-01-15",
        "acquisition_method": "Purchase",
        "description": "A synthetic Minoan bronze votive dagger for automated integration testing.",
        "historical_context": "Created for integration testing purposes only.",
        "provenance": "Synthetic test collection record.",
        "restoration_notes": "Ultrasonic bath simulation in automated test harness.",
        "tags": ["test", "bronze", "dagger", "integration"],
    }

    try:
        # 1. Create artifact (POST)
        create_resp = client.post("/api/artifacts", json=new_artifact)
        assert create_resp.status_code == 201, f"Create failed: {create_resp.text}"
        created_data = create_resp.json()
        assert created_data["artifact_id"] == test_id
        assert created_data["name"] == new_artifact["name"]
        assert "created_at" in created_data

        # 2. Duplicate prevention (POST again -> 409 Conflict)
        dup_resp = client.post("/api/artifacts", json=new_artifact)
        assert dup_resp.status_code == 409
        assert "already exists" in dup_resp.json()["detail"].lower()

        # 3. Retrieve created artifact (GET)
        get_resp = client.get(f"/api/artifacts/{test_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["artifact_id"] == test_id

        # 4. Update artifact (PUT)
        update_payload = {
            "estimated_value_usd": 175000.0,
            "condition": "Pristine",
            "is_on_display": True,
        }
        update_resp = client.put(f"/api/artifacts/{test_id}", json=update_payload)
        assert update_resp.status_code == 200
        updated_data = update_resp.json()
        assert updated_data["estimated_value_usd"] == 175000.0
        assert updated_data["condition"] == "Pristine"
        assert updated_data["is_on_display"] is True
        # Verify unchanged field is preserved
        assert updated_data["name"] == new_artifact["name"]

        # 5. Delete artifact (DELETE)
        del_resp = client.delete(f"/api/artifacts/{test_id}")
        assert del_resp.status_code == 204

        # 6. Verify deleted artifact returns 404
        get_after_del = client.get(f"/api/artifacts/{test_id}")
        assert get_after_del.status_code == 404
    finally:
        client.delete(f"/api/artifacts/{test_id}")


def test_create_artifact_validation_rejection(client: TestClient) -> None:
    """Verifies that invalid payloads are rejected with 422 Unprocessable Entity."""
    # Invalid: missing required fields
    invalid_payload = {
        "artifact_id": "art-invalid-001",
        "name": "Missing Required Fields Artifact",
    }
    response = client.post("/api/artifacts", json=invalid_payload)
    assert response.status_code == 422

    # Invalid: negative value
    negative_val_payload = {
        "artifact_id": "art-invalid-002",
        "accession_number": "ARC-INV-002",
        "name": "Negative Value Artifact",
        "category": "Sculpture",
        "historical_period": "Classical Antiquity",
        "origin_culture": "Greek",
        "origin_location": "Athens",
        "creation_year_earliest": -500,
        "creation_year_latest": -400,
        "medium_materials": ["Marble"],
        "dimensions": {},
        "condition": "Good",
        "is_on_display": True,
        "storage_location": "Vault",
        "estimated_value_usd": -500.0,  # Invalid: ge=0 required
        "acquisition_date": "2024-01-01",
        "acquisition_method": "Purchase",
        "description": "Test",
        "historical_context": "Test",
        "provenance": "Test",
        "restoration_notes": "Test",
        "tags": [],
    }
    neg_response = client.post("/api/artifacts", json=negative_val_payload)
    assert neg_response.status_code == 422

    # Invalid: extra unexpected field (extra="forbid")
    extra_field_payload = dict(negative_val_payload)
    extra_field_payload["estimated_value_usd"] = 5000.0
    extra_field_payload["unauthorized_custom_field"] = "malicious_injection"
    extra_response = client.post("/api/artifacts", json=extra_field_payload)
    assert extra_response.status_code == 422
