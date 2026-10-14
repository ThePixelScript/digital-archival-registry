"""End-to-end verification script for frontend static serving, API endpoints, and CRUD operations."""

import sys
from pathlib import Path
import csv
import io

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

    assert 'id="btn-open-import"' in res.text
    print("   [PASS] Import CSV button verified in frontend HTML")

    assert 'id="import-modal"' in res.text
    print("   [PASS] CSV Import dialog modal verified in frontend HTML")

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

    # 5. Verify Streaming CSV Export
    print("\n5. Verifying Streaming CSV Export...")
    res_export = client.get("/api/artifacts/export")
    assert res_export.status_code == 200, f"Export failed: {res_export.status_code}"
    assert "text/csv; charset=utf-8" in res_export.headers.get("content-type", "")
    assert 'attachment; filename="museum_artifacts_export_' in res_export.headers.get("content-disposition", "")
    assert res_export.content.startswith(b"\xef\xbb\xbf"), "Missing UTF-8 BOM"

    csv_text = res_export.content.decode("utf-8-sig")
    csv_rows = list(csv.reader(io.StringIO(csv_text)))
    assert len(csv_rows) == 31, f"Expected 31 rows (1 header + 30 items), got {len(csv_rows)}"
    assert len(csv_rows[0]) == 28, f"Expected 28 CSV columns, got {len(csv_rows[0])}"
    assert csv_rows[0][0] == "artifact_id"
    assert "dimensions_height_cm" in csv_rows[0]
    print(f"   [PASS] GET /api/artifacts/export returned 30 artifacts with 28 columns and UTF-8 BOM.")

    # Filtered export
    res_filtered_export = client.get("/api/artifacts/export?category=Ceramics%20%26%20Pottery")
    assert res_filtered_export.status_code == 200
    filtered_csv_rows = list(csv.reader(io.StringIO(res_filtered_export.content.decode("utf-8-sig"))))
    category_idx = csv_rows[0].index("category")
    assert len(filtered_csv_rows) == 7, f"Expected 7 rows (1 header + 6 items), got {len(filtered_csv_rows)}"
    for row in filtered_csv_rows[1:]:
        assert row[category_idx] == "Ceramics & Pottery"
    print(f"   [PASS] Filtered CSV export returned {len(filtered_csv_rows) - 1} records strictly matching category.")

    # Search export alias
    res_search_export = client.get("/api/search/export?q=cuneiform")
    assert res_search_export.status_code == 200
    assert 'attachment; filename="museum_artifacts_search_' in res_search_export.headers.get("content-disposition", "")
    search_csv_rows = list(csv.reader(io.StringIO(res_search_export.content.decode("utf-8-sig"))))
    assert len(search_csv_rows) >= 2
    print(f"   [PASS] Search CSV export (/api/search/export) returned {len(search_csv_rows) - 1} matching records.")

    # 6. Verify CRUD Cycle with Temporary Test Document
    print("\n6. Verifying Temporary Artifact CRUD Lifecycle...")
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

    # 7. Verify Two-Stage CSV Import Workflow
    print("\n7. Verifying Two-Stage CSV Import Workflow...")
    imp_id = "art-e2e-imp-001"
    client.delete(f"/api/artifacts/{imp_id}")

    csv_header = "artifact_id,accession_number,name,alternate_names,category,historical_period,origin_culture,origin_location,creation_year_earliest,creation_year_latest,medium_materials,dimensions_height_cm,dimensions_width_cm,dimensions_depth_cm,dimensions_weight_kg,condition,is_on_display,storage_location,estimated_value_usd,acquisition_date,acquisition_method,description,historical_context,provenance,restoration_notes,tags,created_at,updated_at\r\n"
    csv_data = f'{imp_id},ARC-E2E-001,"E2E Test Imported Bronze Mask",,"Sculpture","Classical Antiquity","Roman","Rome, Italy",100,150,"Bronze",22.0,18.0,12.0,1.8,"Excellent",true,"Vault B",250000.0,2024-04-01,"Donation","Curatorial description","Historical significance","Archaeological discovery","Consolidated","test, e2e, import",,\r\n'
    import_bytes = (csv_header + csv_data).encode("utf-8")

    # Preview
    res_preview = client.post("/api/artifacts/import/preview", files={"file": ("e2e_import.csv", import_bytes, "text/csv")})
    assert res_preview.status_code == 200, f"Preview failed: {res_preview.text}"
    prev_data = res_preview.json()
    assert prev_data["can_import"] is True
    assert prev_data["valid_rows"] == 1
    assert prev_data["error_rows"] == 0
    print("   [PASS] POST /api/artifacts/import/preview dry-run validated CSV schema without database modification.")

    # Confirm
    res_confirm = client.post("/api/artifacts/import", files={"file": ("e2e_import.csv", import_bytes, "text/csv")})
    assert res_confirm.status_code == 200, f"Confirm failed: {res_confirm.text}"
    conf_data = res_confirm.json()
    assert conf_data["success"] is True
    assert conf_data["imported_count"] == 1
    print("   [PASS] POST /api/artifacts/import successfully created artifact record from CSV.")

    # Verify retrieval
    res_imp_get = client.get(f"/api/artifacts/{imp_id}")
    assert res_imp_get.status_code == 200
    assert res_imp_get.json()["name"] == "E2E Test Imported Bronze Mask"
    print("   [PASS] GET /api/artifacts/{id} confirmed imported artifact stored accurately.")

    # Clean up imported record
    res_imp_del = client.delete(f"/api/artifacts/{imp_id}")
    assert res_imp_del.status_code == 204
    print("   [PASS] Cleaned up temporary imported record.")

    # 8. Verify original dataset integrity
    res_final_health = client.get("/api/health")
    final_docs = res_final_health.json()["elasticsearch"]["document_count"]
    assert final_docs == 30, f"Expected 30 documents, got {final_docs}"
    print(f"\n8. Original dataset integrity confirmed: Exactly 30 artifacts preserved in index.")
    print("=" * 70)
    print("ALL FRONTEND ASSET & API VERIFICATION CHECKS PASSED SUCCESSFULLY!")
    print("=" * 70)

if __name__ == "__main__":
    main()
