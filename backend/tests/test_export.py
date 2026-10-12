"""Comprehensive test suite for streaming CSV export, filtering, and formula injection protection."""

import csv
import io
from fastapi.testclient import TestClient
from app.services.artifact_service import CSV_COLUMNS, sanitize_csv_value


def test_sanitize_csv_value_formula_injection() -> None:
    """Verifies that formula trigger characters are safely neutralized with a leading apostrophe."""
    # Text starting with formula characters
    assert sanitize_csv_value("=1+1") == "'=1+1"
    assert sanitize_csv_value("+2+2") == "'+2+2"
    assert sanitize_csv_value("-5-5") == "'-5-5"
    assert sanitize_csv_value("@SUM(A1:A10)") == "'@SUM(A1:A10)"
    assert sanitize_csv_value("\tcmd") == "'\tcmd"
    assert sanitize_csv_value("\rcmd") == "'\rcmd"
    assert sanitize_csv_value("%cmd") == "'%cmd"
    assert sanitize_csv_value("  =HYPERLINK('http://evil.com')") == "'  =HYPERLINK('http://evil.com')"

    # Genuine numbers must not be prefixed
    assert sanitize_csv_value(-1750) == -1750
    assert sanitize_csv_value(2024) == 2024
    assert sanitize_csv_value(-3.14) == -3.14
    assert sanitize_csv_value(0) == 0

    # Booleans and None
    assert sanitize_csv_value(True) == "true"
    assert sanitize_csv_value(False) == "false"
    assert sanitize_csv_value(None) == ""

    # Normal strings
    assert sanitize_csv_value("Code of Hammurabi") == "Code of Hammurabi"
    assert sanitize_csv_value("Bronze, Gold") == "Bronze, Gold"

    # Lists
    assert sanitize_csv_value(["=malicious", "safe"]) == "'=malicious, safe"
    assert sanitize_csv_value(["safe1", "safe2"]) == "safe1, safe2"


def test_export_csv_headers_and_content_type(client: TestClient) -> None:
    """Verifies proper response headers and UTF-8 BOM encoding on CSV downloads."""
    response = client.get("/api/artifacts/export")
    assert response.status_code == 200
    assert "text/csv" in response.headers.get("content-type", "")
    assert "charset=utf-8" in response.headers.get("content-type", "").lower()
    disposition = response.headers.get("content-disposition", "")
    assert "attachment" in disposition
    assert "filename=" in disposition
    assert ".csv" in disposition
    assert response.text.startswith("\ufeff")


def test_export_csv_stable_columns(client: TestClient) -> None:
    """Verifies that the CSV header row exactly matches the documented stable column contract."""
    response = client.get("/api/artifacts/export")
    assert response.status_code == 200
    content = response.text.lstrip("\ufeff")
    reader = csv.reader(io.StringIO(content))
    header_row = next(reader)
    assert header_row == CSV_COLUMNS
    assert len(header_row) == 28


def test_export_csv_full_result_beyond_visible_page(client: TestClient) -> None:
    """Verifies that the export retrieves the complete matching dataset (30 records), not just page 1 (10)."""
    response = client.get("/api/artifacts/export")
    assert response.status_code == 200
    content = response.text.lstrip("\ufeff")
    reader = csv.reader(io.StringIO(content))
    rows = list(reader)
    header = rows[0]
    data_rows = rows[1:]
    assert len(header) == 28
    assert len(data_rows) == 30, f"Expected all 30 sample artifacts, got {len(data_rows)}"
    id_index = header.index("artifact_id")
    exported_ids = {r[id_index] for r in data_rows}
    assert "art-001" in exported_ids
    assert "art-030" in exported_ids


def test_export_csv_filtered_category(client: TestClient) -> None:
    """Verifies that category filter is accurately applied to full CSV export."""
    response = client.get("/api/artifacts/export?category=Ceramics%20%26%20Pottery")
    assert response.status_code == 200
    content = response.text.lstrip("\ufeff")
    reader = csv.reader(io.StringIO(content))
    rows = list(reader)
    header = rows[0]
    data_rows = rows[1:]
    cat_index = header.index("category")
    assert len(data_rows) == 6
    for r in data_rows:
        assert r[cat_index] == "Ceramics & Pottery"


def test_export_csv_filtered_historical_period(client: TestClient) -> None:
    """Verifies that historical period filter is accurately applied to CSV export."""
    response = client.get("/api/artifacts/export?historical_period=Classical%20Antiquity")
    assert response.status_code == 200
    content = response.text.lstrip("\ufeff")
    reader = csv.reader(io.StringIO(content))
    rows = list(reader)
    header = rows[0]
    data_rows = rows[1:]
    period_index = header.index("historical_period")
    assert len(data_rows) == 7
    for r in data_rows:
        assert r[period_index] == "Classical Antiquity"


def test_export_csv_filtered_is_on_display(client: TestClient) -> None:
    """Verifies that gallery display status filter works on CSV export."""
    response = client.get("/api/artifacts/export?is_on_display=false")
    assert response.status_code == 200
    content = response.text.lstrip("\ufeff")
    reader = csv.reader(io.StringIO(content))
    rows = list(reader)
    header = rows[0]
    data_rows = rows[1:]
    display_index = header.index("is_on_display")
    assert len(data_rows) > 0
    for r in data_rows:
        assert r[display_index] == "false"


def test_export_csv_search_query_matching(client: TestClient) -> None:
    """Verifies that full-text keyword search filters the exported CSV correctly."""
    response = client.get("/api/artifacts/export?q=cuneiform")
    assert response.status_code == 200
    content = response.text.lstrip("\ufeff")
    reader = csv.reader(io.StringIO(content))
    rows = list(reader)
    data_rows = rows[1:]
    assert len(data_rows) == 2
    id_index = rows[0].index("artifact_id")
    exported_ids = {r[id_index] for r in data_rows}
    assert "art-001" in exported_ids


def test_export_csv_fuzzy_search(client: TestClient) -> None:
    """Verifies that fuzzy typo tolerance applies when requested on export."""
    response = client.get("/api/artifacts/export?q=astrolbae&fuzzy=true")
    assert response.status_code == 200
    content = response.text.lstrip("\ufeff")
    reader = csv.reader(io.StringIO(content))
    rows = list(reader)
    data_rows = rows[1:]
    assert len(data_rows) == 2


def test_export_csv_empty_results(client: TestClient) -> None:
    """Verifies that an empty matching result set outputs valid CSV headers with zero data rows."""
    response = client.get("/api/artifacts/export?category=NonExistentCategory999")
    assert response.status_code == 200
    content = response.text.lstrip("\ufeff")
    reader = csv.reader(io.StringIO(content))
    rows = list(reader)
    assert len(rows) == 1
    assert rows[0] == CSV_COLUMNS


def test_export_csv_invalid_filter_validation(client: TestClient) -> None:
    """Verifies that invalid filter arguments return HTTP 422 Unprocessable Entity."""
    response = client.get("/api/artifacts/export?sort_order=invalid_direction")
    assert response.status_code == 422


def test_export_search_alias_endpoint(client: TestClient) -> None:
    """Verifies that /api/search/export serves the same streaming CSV format."""
    response = client.get("/api/search/export?q=cuneiform")
    assert response.status_code == 200
    assert "text/csv" in response.headers.get("content-type", "")
    content = response.text.lstrip("\ufeff")
    reader = csv.reader(io.StringIO(content))
    rows = list(reader)
    assert len(rows) == 3  # Header + 2 data rows


def test_export_csv_special_characters_quotes_and_newlines(client: TestClient) -> None:
    """Verifies RFC 4180 escaping of double quotes, commas, multiline descriptions, and Unicode."""
    test_id = "test-export-special-001"
    payload = {
        "artifact_id": test_id,
        "accession_number": "ARC-2024-EXP-001",
        "name": 'SpecimenAlpha with "Double Quotes", and, Commas',
        "alternate_names": 'Designation "Alpha" & "Beta"',
        "category": "Sculpture & Statuary",
        "historical_period": "Classical Greece",
        "origin_culture": "Ancient Greece",
        "origin_location": "Athens, Greece",
        "creation_year_earliest": -450,
        "creation_year_latest": -400,
        "medium_materials": ['Parian Marble, "Finest Grade"', "Gold leaf"],
        "dimensions": {
            "height_cm": 180.5,
            "width_cm": 60.0,
            "depth_cm": 45.0,
            "weight_kg": 320.0,
        },
        "condition": "Good",
        "is_on_display": True,
        "storage_location": "Gallery 3, Vitrine 4",
        "estimated_value_usd": 1500000.0,
        "acquisition_date": "2024-01-15",
        "acquisition_method": "Museum Purchase",
        "description": "Paragraph 1: Discovered in Athens.\nParagraph 2: Features intricate \"chiton\" drapery, carved in relief.",
        "historical_context": "Classical golden age sculpture;\nillustrates high-classical proportions.",
        "provenance": "Excavated 1890, private collection in London, acquired 2024.",
        "restoration_notes": "Cleaned with non-ionic detergent; stabilized with microcrystalline wax.",
        "tags": ["sculpture, classical", 'marble "parian"', "statue"],
    }

    try:
        create_res = client.post("/api/artifacts", json=payload)
        assert create_res.status_code == 201

        export_res = client.get("/api/artifacts/export?q=SpecimenAlpha")
        assert export_res.status_code == 200

        content = export_res.text.lstrip("\ufeff")
        reader = csv.reader(io.StringIO(content))
        rows = list(reader)
        header = rows[0]
        data_rows = rows[1:]

        assert len(data_rows) == 1
        row = data_rows[0]

        name_idx = header.index("name")
        desc_idx = header.index("description")
        year_idx = header.index("creation_year_earliest")
        height_idx = header.index("dimensions_height_cm")
        materials_idx = header.index("medium_materials")

        assert row[name_idx] == 'SpecimenAlpha with "Double Quotes", and, Commas'
        assert row[desc_idx] == "Paragraph 1: Discovered in Athens.\nParagraph 2: Features intricate \"chiton\" drapery, carved in relief."
        assert row[year_idx] == "-450"
        assert row[height_idx] == "180.5"
        assert 'Parian Marble, "Finest Grade"' in row[materials_idx]
    finally:
        client.delete(f"/api/artifacts/{test_id}")


def test_export_csv_unicode_preservation(client: TestClient) -> None:
    """Verifies that non-Latin scripts, hieroglyphs, and diacritics are preserved without corruption."""
    test_id = "test-export-unicode-001"
    unicode_name = "SpecimenBeta 𓀀 Ägyptisches Museum מقتنيات музей"
    payload = {
        "artifact_id": test_id,
        "accession_number": "ARC-2024-EXP-UNI",
        "name": unicode_name,
        "alternate_names": "Золотое ожерелье / قلادة ذهبية",
        "category": "Jewelry & Adornment",
        "historical_period": "New Kingdom Egypt",
        "origin_culture": "Ancient Egyptian",
        "origin_location": "Valley of the Kings, Luxor, Egypt",
        "creation_year_earliest": -1330,
        "creation_year_latest": -1320,
        "medium_materials": ["Gold", "Lapis lazuli", "Carnelian"],
        "dimensions": {
            "height_cm": 15.0,
            "width_cm": 12.0,
            "depth_cm": 1.5,
            "weight_kg": 0.85,
        },
        "condition": "Pristine",
        "is_on_display": True,
        "storage_location": "Gold Treasury Vitrine 1",
        "estimated_value_usd": 12000000.0,
        "acquisition_date": "2024-02-01",
        "acquisition_method": "State Transfer",
        "description": "Funerary pectoral featuring winged scarab and hieroglyphic inscription: 𓋹 𓍑 𓋴.",
        "historical_context": "Created during the 18th Dynasty under Tutankhamun's reign.",
        "provenance": "Tomb KV62, discovered 1922.",
        "restoration_notes": "Surface stabilized; pristine preservation.",
        "tags": ["gold", "tomb", "hieroglyphics", "18th-dynasty"],
    }

    try:
        create_res = client.post("/api/artifacts", json=payload)
        assert create_res.status_code == 201

        export_res = client.get("/api/artifacts/export?q=SpecimenBeta")
        assert export_res.status_code == 200

        content = export_res.text.lstrip("\ufeff")
        reader = csv.reader(io.StringIO(content))
        rows = list(reader)
        row = rows[1]

        name_idx = rows[0].index("name")
        desc_idx = rows[0].index("description")

        assert row[name_idx] == unicode_name
        assert "𓋹 𓍑 𓋴" in row[desc_idx]
    finally:
        client.delete(f"/api/artifacts/{test_id}")


def test_export_csv_formula_injection_defense(client: TestClient) -> None:
    """Verifies spreadsheet formula injection defenses (CWE-1236) on stored artifact text fields."""
    test_id = "test-export-formula-001"
    payload = {
        "artifact_id": test_id,
        "accession_number": "ARC-2024-EXP-FORMULA",
        "name": "=1+1",
        "alternate_names": "+CMD|' /C calc'!A0",
        "category": "Tool & Implement",
        "historical_period": "Iron Age",
        "origin_culture": "Celtic",
        "origin_location": "Hallstatt, Austria",
        "creation_year_earliest": -750,
        "creation_year_latest": -600,
        "medium_materials": ["Iron", "=Wood"],
        "dimensions": {
            "height_cm": 30.0,
            "width_cm": 5.0,
            "depth_cm": 3.0,
            "weight_kg": 0.9,
        },
        "condition": "Fair",
        "is_on_display": False,
        "storage_location": "Vault Box 12",
        "estimated_value_usd": 45000.0,
        "acquisition_date": "2024-03-01",
        "acquisition_method": "Bequest",
        "description": "@SUM(1,2) SpecimenGamma",
        "historical_context": "   =HYPERLINK('http://malicious.example.com')",
        "provenance": "-Stolen and returned",
        "restoration_notes": "\tcorrosion treatment performed",
        "tags": ["=exploit", "+tag", "-tag", "@tag"],
    }

    try:
        create_res = client.post("/api/artifacts", json=payload)
        assert create_res.status_code == 201

        export_res = client.get("/api/artifacts/export?q=SpecimenGamma")
        assert export_res.status_code == 200

        content = export_res.text.lstrip("\ufeff")
        reader = csv.reader(io.StringIO(content))
        rows = list(reader)
        header = rows[0]
        row = rows[1]

        name_idx = header.index("name")
        alt_idx = header.index("alternate_names")
        desc_idx = header.index("description")
        context_idx = header.index("historical_context")
        prov_idx = header.index("provenance")
        resto_idx = header.index("restoration_notes")
        year_idx = header.index("creation_year_earliest")
        val_idx = header.index("estimated_value_usd")
        tags_idx = header.index("tags")

        assert row[name_idx] == "'=1+1"
        assert row[alt_idx] == "'+CMD|' /C calc'!A0"
        assert row[desc_idx] == "'@SUM(1,2) SpecimenGamma"
        assert row[context_idx] == "'   =HYPERLINK('http://malicious.example.com')"
        assert row[prov_idx] == "'-Stolen and returned"
        assert row[resto_idx] == "'\tcorrosion treatment performed"

        assert "'=exploit" in row[tags_idx]
        assert "'+tag" in row[tags_idx]

        assert row[year_idx] == "-750"
        assert row[val_idx] == "45000.0"
    finally:
        client.delete(f"/api/artifacts/{test_id}")
