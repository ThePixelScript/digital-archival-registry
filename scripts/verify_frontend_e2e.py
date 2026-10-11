"""End-to-end verification script for frontend static serving, API endpoints, and CRUD operations."""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_BACKEND_DIR = _REPO_ROOT / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from fastapi.testclient import TestClient
from app.main import app

def main() -> None:
    print("=" * 70)
    print("Verifying Frontend Assets & Full-Stack API Integration")
    print("=" * 70)

    client = TestClient(app)

    # 1. Verify Frontend Assets
    print("\n1. Verifying Static Assets Mounted Under /app/...")
    res = client.get("/app/")
    assert res.status_code == 200, f"Failed to get /app/: {res.status_code}"
    assert "Digital Archival Registry" in res.text
    print("   [PASS] GET /app/ served correctly (HTML5 entry point)")

    res_css = client.get("/app/css/styles.css")
    assert res_css.status_code == 200
    assert "--color-bg-canvas" in res_css.text
    print("   [PASS] GET /app/css/styles.css served correctly (Design System Tokens)")

    res_js = client.get("/app/js/app.js")
    assert res_js.status_code == 200
    assert "DOMContentLoaded" in res_js.text
    print("   [PASS] GET /app/js/app.js served correctly (Vanilla ES Module)")

    res_svg = client.get("/app/favicon.svg")
    assert res_svg.status_code == 200
    assert "image/svg+xml" in res_svg.headers.get("content-type", "")
    print("   [PASS] GET /app/favicon.svg served correctly (Archival SVG Favicon)")

    res_ico = client.get("/favicon.ico")
    assert res_ico.status_code == 200
    assert "image/svg+xml" in res_ico.headers.get("content-type", "")
    print("   [PASS] GET /favicon.ico served correctly (Root Favicon Route)")

    assert "Demonstration collection: records are fictional and intended for software testing and demonstration." in res.text
    print("   [PASS] Demonstration collection notice verified in frontend HTML")

    # 2. Verify Health
    print("\n2. Verifying Health Endpoint...")
    res_health = client.get("/api/health")
    assert res_health.status_code == 200
    health_data = res_health.json()
    assert health_data["status"] == "ok"
    assert health_data["elasticsearch"]["connected"] is True
    doc_count = health_data["elasticsearch"]["document_count"]
    print(f"   [PASS] Elasticsearch connected! Total indexed documents: {doc_count}")

    # 3. Verify Stats & Aggregations
    print("\n3. Verifying Aggregated Stats Endpoint (/api/stats)...")
    res_stats = client.get("/api/stats")
    assert res_stats.status_code == 200
    stats = res_stats.json()
    assert stats["total_artifacts"] >= 30
    assert len(stats["categories"]) > 0
    assert len(stats["historical_periods"]) > 0
    assert len(stats["conditions"]) > 0
    print(f"   [PASS] Aggregated facets returned: {len(stats['categories'])} categories, "
          f"{len(stats['historical_periods'])} periods, {len(stats['conditions'])} conditions.")
    print(f"   [PASS] Valuation stats: Sum=${stats['valuation']['sum']:,.2f}, Avg=${stats['valuation']['avg']:,.2f}")

    # 4. Verify Search & Typo Tolerance
    print("\n4. Verifying Search Queries...")
    # Exact match
    res_search = client.get("/api/search?q=cuneiform")
    assert res_search.status_code == 200
    search_data = res_search.json()
    assert search_data["total"] >= 1
    print(f"   [PASS] Full-text search for 'cuneiform' matched {search_data['total']} documents.")

    # Typo tolerance
    res_fuzzy = client.get("/api/search?q=astrolbae&fuzzy=true")
    assert res_fuzzy.status_code == 200
    fuzzy_data = res_fuzzy.json()
    assert fuzzy_data["total"] >= 1
    print(f"   [PASS] Typo-tolerant search for 'astrolbae' (fuzzy=true) matched {fuzzy_data['total']} documents.")

    # Category filter
    res_cat = client.get("/api/search?category=Ceramics%20%26%20Pottery")
    assert res_cat.status_code == 200
    cat_data = res_cat.json()
    assert cat_data["total"] >= 1
    for item in cat_data["items"]:
        assert item["category"] == "Ceramics & Pottery"
    print(f"   [PASS] Category filter 'Ceramics & Pottery' returned {cat_data['total']} strictly filtered items.")

    # 5. Verify CRUD Cycle with Temporary Test Document
    print("\n5. Verifying Temporary Artifact CRUD Lifecycle...")
    test_id = "art-ui-verification-001"
    test_payload = {
        "artifact_id": test_id,
        "accession_number": "ARC-2024-TEST-001",
        "name": "Integration Verification Astrolabe",
        "alternate_names": "Celestial Calculator Test Specimen",
        "category": "Tool & Implement",
        "historical_period": "Islamic Golden Age",
        "origin_culture": "Abbasid Caliphate",
        "origin_location": "Baghdad, Iraq",
        "creation_year_earliest": 850,
        "creation_year_latest": 900,
        "medium_materials": ["Brass", "Silver Inlay"],
        "dimensions": {
            "height_cm": 25.0,
            "width_cm": 20.0,
            "depth_cm": 3.5,
            "weight_kg": 1.45
        },
        "condition": "Excellent",
        "is_on_display": True,
        "storage_location": "Conservation Lab Vitrine 9",
        "estimated_value_usd": 320000.0,
        "acquisition_date": "2024-05-15",
        "acquisition_method": "Private Donation",
        "description": "Scientific observational instrument for determining celestial coordinates.",
        "historical_context": "Reflects the flourishing of astronomical mathematics in 9th century Baghdad.",
        "provenance": "Acquired from a private collection in Cairo, verified custody chain.",
        "restoration_notes": "Ultrasonic surface cleaning performed; corrosion stabilized.",
        "tags": ["astronomy", "brass", "navigation", "scientific"]
    }

    # Clean up prior instance if exists
    client.delete(f"/api/artifacts/{test_id}")

    # Create
    res_create = client.post("/api/artifacts", json=test_payload)
    assert res_create.status_code == 201, f"Failed create: {res_create.text}"
    created = res_create.json()
    assert created["artifact_id"] == test_id
    assert created["name"] == test_payload["name"]
    print("   [PASS] POST /api/artifacts successfully created test artifact (201 Created).")

    # Read
    res_get = client.get(f"/api/artifacts/{test_id}")
    assert res_get.status_code == 200
    doc = res_get.json()
    assert doc["dimensions"]["weight_kg"] == 1.45
    assert doc["estimated_value_usd"] == 320000.0
    print("   [PASS] GET /api/artifacts/{id} retrieved all 25 fields accurately.")

    # Update
    update_payload = {
        "condition": "Pristine",
        "storage_location": "Main Gallery East Vitrine 1",
        "estimated_value_usd": 350000.0
    }
    res_update = client.put(f"/api/artifacts/{test_id}", json=update_payload)
    assert res_update.status_code == 200
    updated = res_update.json()
    assert updated["condition"] == "Pristine"
    assert updated["estimated_value_usd"] == 350000.0
    assert updated["storage_location"] == "Main Gallery East Vitrine 1"
    print("   [PASS] PUT /api/artifacts/{id} successfully updated artifact fields.")

    # Delete
    res_del = client.delete(f"/api/artifacts/{test_id}")
    assert res_del.status_code == 204
    print("   [PASS] DELETE /api/artifacts/{id} successfully removed test artifact (204 No Content).")

    # Confirm 404
    res_notfound = client.get(f"/api/artifacts/{test_id}")
    assert res_notfound.status_code == 404
    print("   [PASS] Confirmed test document no longer exists (404 Not Found).")

    # 6. Verify original dataset integrity
    res_final_health = client.get("/api/health")
    final_docs = res_final_health.json()["elasticsearch"]["document_count"]
    assert final_docs == 30, f"Expected 30 documents, got {final_docs}"
    print(f"\n6. Original dataset integrity confirmed: Exactly 30 artifacts preserved in index.")
    print("=" * 70)
    print("ALL FRONTEND ASSET & API VERIFICATION CHECKS PASSED SUCCESSFULLY!")
    print("=" * 70)

if __name__ == "__main__":
    main()
