"""Comprehensive test suite verifying two-stage CSV import validation and bulk ingestion."""

import csv
from datetime import datetime, timezone
import io
from typing import Any, Dict, List, Optional
import pytest
from fastapi.testclient import TestClient
from app.core.config import settings
from app.core.elasticsearch import get_elasticsearch_client
from app.services.artifact_service import (
    CSV_COLUMNS,
    CSV_REQUIRED_COLUMNS,
    MAX_IMPORT_FILE_SIZE_BYTES,
    MAX_IMPORT_ROW_COUNT,
    execute_csv_import,
    parse_csv_boolean,
    parse_csv_list,
    unescape_csv_value,
    validate_csv_import,
)


def _make_artifact_dict(**overrides: Any) -> Dict[str, Any]:
    """Generates a complete, valid artifact dictionary matching the CSV schema."""
    base: Dict[str, Any] = {
        "artifact_id": "art-test-imp-901",
        "accession_number": "ARC-TEST-901",
        "name": "Integration Votive Bronze Chariot",
        "alternate_names": "Miniature Ceremonial Chariot",
        "category": "Metalwork & Arms",
        "historical_period": "Bronze Age",
        "origin_culture": "Mycenaean",
        "origin_location": "Argolid, Peloponnese",
        "creation_year_earliest": -1350,
        "creation_year_latest": -1250,
        "medium_materials": "Cast bronze, Electrum inlay",
        "dimensions_height_cm": 18.5,
        "dimensions_width_cm": 12.0,
        "dimensions_depth_cm": 24.0,
        "dimensions_weight_kg": 2.15,
        "condition": "Excellent",
        "is_on_display": True,
        "storage_location": "Vault Locker B-14",
        "estimated_value_usd": 420000.0,
        "acquisition_date": "2024-03-20",
        "acquisition_method": "Bequest",
        "description": "Cast bronze model of a two-horse war chariot.",
        "historical_context": "Found in a tholos tomb context representing elite militarism.",
        "provenance": "Excavated at Mycenae in 1895; private collection bequest 2024.",
        "restoration_notes": "Stabilized in micro-crystalline wax enclosure.",
        "tags": "bronze, mycenaean, chariot, warrior",
        "created_at": "2024-03-20T12:00:00Z",
        "updated_at": "2024-03-20T12:00:00Z",
    }
    base.update(overrides)
    return base


def _build_csv_bytes(
    rows: List[Dict[str, Any]],
    headers: Optional[List[str]] = None,
    encoding: str = "utf-8",
    bom: bool = False,
) -> bytes:
    """Serializes a list of dictionaries into RFC 4180 CSV bytes."""
    cols = headers if headers is not None else CSV_COLUMNS
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=cols, lineterminator="\r\n")
    writer.writeheader()
    for row in rows:
        row_dict = {c: row.get(c, "") for c in cols}
        writer.writerow(row_dict)
    raw = buf.getvalue().encode(encoding)
    return (b"\xef\xbb\xbf" + raw) if bom else raw


def test_unescape_csv_value_unit() -> None:
    """Verifies that formula injection escaping is cleanly reversed on text fields."""
    assert unescape_csv_value(None) == ""
    assert unescape_csv_value(123) == 123
    assert unescape_csv_value("'-1323") == "-1323"
    assert unescape_csv_value("'+44 7911 123456") == "+44 7911 123456"
    assert unescape_csv_value("'=SUM(A1:B10)") == "=SUM(A1:B10)"
    assert unescape_csv_value("'@admin_directive") == "@admin_directive"
    assert unescape_csv_value("Standard Name") == "Standard Name"
    # An apostrophe not followed by a formula prefix is preserved
    assert unescape_csv_value("'King Tut'") == "'King Tut'"


def test_parse_csv_boolean_unit() -> None:
    """Verifies boolean parsing for CSV string values."""
    assert parse_csv_boolean(True) is True
    assert parse_csv_boolean(False) is False
    assert parse_csv_boolean(None) is True
    assert parse_csv_boolean("") is True
    assert parse_csv_boolean("true") is True
    assert parse_csv_boolean("TRUE") is True
    assert parse_csv_boolean("1") is True
    assert parse_csv_boolean("yes") is True
    assert parse_csv_boolean("false") is False
    assert parse_csv_boolean("FALSE") is False
    assert parse_csv_boolean("0") is False
    assert parse_csv_boolean("no") is False
    with pytest.raises(ValueError, match="Invalid boolean"):
        parse_csv_boolean("unknown")


def test_parse_csv_list_unit() -> None:
    """Verifies parsing of comma-delimited strings with whitespace and quotes."""
    assert parse_csv_list("") == []
    assert parse_csv_list(None) == []
    assert parse_csv_list("gold, lapis lazuli, bronze") == ["gold", "lapis lazuli", "bronze"]
    assert parse_csv_list('"Gold, 24k", Silver, "Bronze, cast"') == ["Gold, 24k", "Silver", "Bronze, cast"]


def test_import_preview_valid_no_db_modification(client: TestClient) -> None:
    """Verifies dry-run preview succeeds for valid records without modifying the Elasticsearch index."""
    es = get_elasticsearch_client()
    count_before = es.count(index=settings.ELASTICSEARCH_INDEX)["count"]

    row1 = _make_artifact_dict(artifact_id="art-test-prev-001", accession_number="ARC-PREV-001")
    row2 = _make_artifact_dict(artifact_id="art-test-prev-002", accession_number="ARC-PREV-002")
    csv_bytes = _build_csv_bytes([row1, row2])

    resp = client.post("/api/artifacts/import/preview", files={"file": ("test.csv", csv_bytes, "text/csv")})
    assert resp.status_code == 200, f"Preview failed: {resp.text}"
    data = resp.json()

    assert data["total_rows"] == 2
    assert data["valid_rows"] == 2
    assert data["error_rows"] == 0
    assert data["duplicate_file_ids"] == []
    assert data["existing_collection_ids"] == []
    assert data["errors"] == []
    assert data["can_import"] is True
    assert len(data["preview_records"]) == 2
    assert data["preview_records"][0]["artifact_id"] == "art-test-prev-001"

    # Crucial assertion: Elasticsearch document count must be completely unchanged
    count_after = es.count(index=settings.ELASTICSEARCH_INDEX)["count"]
    assert count_after == count_before
    # Neither ID should exist in the database
    assert not es.exists(index=settings.ELASTICSEARCH_INDEX, id="art-test-prev-001")
    assert not es.exists(index=settings.ELASTICSEARCH_INDEX, id="art-test-prev-002")


def test_import_confirm_successful_creation_and_cleanup(client: TestClient) -> None:
    """Verifies confirming import creates new artifacts in Elasticsearch and cleans up properly."""
    es = get_elasticsearch_client()
    id1 = "art-test-conf-001"
    id2 = "art-test-conf-002"

    row1 = _make_artifact_dict(artifact_id=id1, accession_number="ARC-CONF-001", name="Test Import Bronze Statuette")
    row2 = _make_artifact_dict(artifact_id=id2, accession_number="ARC-CONF-002", name="Test Import Silver Goblet")
    csv_bytes = _build_csv_bytes([row1, row2])

    try:
        # Confirm import
        resp = client.post("/api/artifacts/import", files={"file": ("import.csv", csv_bytes, "text/csv")})
        assert resp.status_code == 200, f"Import failed: {resp.text}"
        result = resp.json()

        assert result["success"] is True
        assert result["imported_count"] == 2
        assert result["total_rows"] == 2
        assert id1 in result["imported_ids"]
        assert id2 in result["imported_ids"]

        # Verify documents now exist in Elasticsearch and can be retrieved via GET
        get1 = client.get(f"/api/artifacts/{id1}")
        assert get1.status_code == 200
        assert get1.json()["name"] == "Test Import Bronze Statuette"

        get2 = client.get(f"/api/artifacts/{id2}")
        assert get2.status_code == 200
        assert get2.json()["name"] == "Test Import Silver Goblet"
    finally:
        # Cleanup created records to maintain the pristine 30 sample artifacts
        try:
            es.delete(index=settings.ELASTICSEARCH_INDEX, id=id1, refresh=True)
        except Exception:
            pass
        try:
            es.delete(index=settings.ELASTICSEARCH_INDEX, id=id2, refresh=True)
        except Exception:
            pass


def test_import_missing_required_headers(client: TestClient) -> None:
    """Verifies that missing required columns in the CSV header are flagged on row 1."""
    # Omit 'accession_number' and 'name'
    incomplete_headers = [c for c in CSV_COLUMNS if c not in ("accession_number", "name")]
    row = _make_artifact_dict()
    csv_bytes = _build_csv_bytes([row], headers=incomplete_headers)

    resp = client.post("/api/artifacts/import/preview", files={"file": ("invalid_headers.csv", csv_bytes, "text/csv")})
    assert resp.status_code == 200
    data = resp.json()

    assert data["can_import"] is False
    assert len(data["errors"]) > 0
    assert data["errors"][0]["row_number"] == 1
    assert "missing required column(s)" in data["errors"][0]["message"].lower()
    assert "name" in data["errors"][0]["message"]
    assert "accession_number" in data["errors"][0]["message"]


def test_import_missing_required_row_fields(client: TestClient) -> None:
    """Verifies that empty required fields on specific data rows are caught and reported."""
    row = _make_artifact_dict(name="", storage_location="   ", description="")
    csv_bytes = _build_csv_bytes([row])

    resp = client.post("/api/artifacts/import/preview", files={"file": ("missing_fields.csv", csv_bytes, "text/csv")})
    assert resp.status_code == 200
    data = resp.json()

    assert data["can_import"] is False
    assert data["error_rows"] == 1
    field_errors = {e["field"] for e in data["errors"]}
    assert "name" in field_errors
    assert "storage_location" in field_errors
    assert "description" in field_errors


def test_import_invalid_data_types_and_ranges(client: TestClient) -> None:
    """Verifies validation of invalid numbers, booleans, dates, year ordering, and dimensions."""
    row = _make_artifact_dict(
        estimated_value_usd="-100.0",
        creation_year_earliest="not_a_year",
        creation_year_latest=2000,
        acquisition_date="2024/01/15",  # Invalid date format
        is_on_display="maybe",  # Invalid boolean
        dimensions_height_cm="-5.5",  # Negative dimension
    )
    csv_bytes = _build_csv_bytes([row])

    resp = client.post("/api/artifacts/import/preview", files={"file": ("invalid_types.csv", csv_bytes, "text/csv")})
    assert resp.status_code == 200
    data = resp.json()

    assert data["can_import"] is False
    field_errors = {e["field"] for e in data["errors"]}
    assert "estimated_value_usd" in field_errors
    assert "creation_year_earliest" in field_errors
    assert "acquisition_date" in field_errors
    assert "is_on_display" in field_errors
    assert "dimensions_height_cm" in field_errors


def test_import_year_earliest_greater_than_latest(client: TestClient) -> None:
    """Verifies rejection when earliest creation year is greater than latest creation year."""
    row = _make_artifact_dict(creation_year_earliest=500, creation_year_latest=400)
    csv_bytes = _build_csv_bytes([row])

    resp = client.post("/api/artifacts/import/preview", files={"file": ("year_mismatch.csv", csv_bytes, "text/csv")})
    assert resp.status_code == 200
    data = resp.json()

    assert data["can_import"] is False
    err_msgs = [e["message"] for e in data["errors"]]
    assert any("cannot be greater than" in msg for msg in err_msgs)


def test_import_duplicate_ids_within_file(client: TestClient) -> None:
    """Verifies that duplicate artifact IDs occurring within the same CSV file are caught."""
    dup_id = "art-test-in-file-dup"
    row1 = _make_artifact_dict(artifact_id=dup_id, accession_number="ARC-DUP-001", name="First Occurrence")
    row2 = _make_artifact_dict(artifact_id=dup_id, accession_number="ARC-DUP-002", name="Second Occurrence")
    csv_bytes = _build_csv_bytes([row1, row2])

    resp = client.post("/api/artifacts/import/preview", files={"file": ("duplicate_ids.csv", csv_bytes, "text/csv")})
    assert resp.status_code == 200
    data = resp.json()

    assert data["can_import"] is False
    assert dup_id in data["duplicate_file_ids"]
    assert any(e["row_number"] == 2 and e["artifact_id"] == dup_id for e in data["errors"])
    assert any(e["row_number"] == 3 and e["artifact_id"] == dup_id for e in data["errors"])


def test_import_existing_collection_id_conflict(client: TestClient) -> None:
    """Verifies that an artifact ID already present in Elasticsearch triggers a conflict error."""
    # 'art-001' is a known existing artifact in the archival collection
    row = _make_artifact_dict(artifact_id="art-001", accession_number="ARC-CONFLICT-001")
    csv_bytes = _build_csv_bytes([row])

    resp = client.post("/api/artifacts/import/preview", files={"file": ("existing_conflict.csv", csv_bytes, "text/csv")})
    assert resp.status_code == 200
    data = resp.json()

    assert data["can_import"] is False
    assert "art-001" in data["existing_collection_ids"]
    assert any("already exists" in e["message"].lower() for e in data["errors"])


def test_import_existing_records_never_overwritten(client: TestClient) -> None:
    """Verifies that existing collection records are never overwritten when an import is attempted."""
    es = get_elasticsearch_client()
    original_doc = es.get(index=settings.ELASTICSEARCH_INDEX, id="art-001")["_source"]
    original_name = original_doc["name"]

    # Attempt to import an artifact with id="art-001" but a malicious/altered name
    altered_row = _make_artifact_dict(artifact_id="art-001", name="MALICIOUS OVERWRITE ATTEMPT")
    csv_bytes = _build_csv_bytes([altered_row])

    resp = client.post("/api/artifacts/import", files={"file": ("overwrite.csv", csv_bytes, "text/csv")})
    assert resp.status_code == 409

    # Verify that art-001 in Elasticsearch is completely intact and unaltered
    current_doc = es.get(index=settings.ELASTICSEARCH_INDEX, id="art-001")["_source"]
    assert current_doc["name"] == original_name


def test_import_confirmation_revalidation_detects_intervening_changes(client: TestClient) -> None:
    """Verifies that if a record is added to the collection between preview and confirm, confirm aborts."""
    es = get_elasticsearch_client()
    race_id = "art-test-race-check-001"
    row = _make_artifact_dict(artifact_id=race_id, accession_number="ARC-RACE-001")
    csv_bytes = _build_csv_bytes([row])

    # 1. Preview passes cleanly
    prev_resp = client.post("/api/artifacts/import/preview", files={"file": ("race.csv", csv_bytes, "text/csv")})
    assert prev_resp.status_code == 200
    assert prev_resp.json()["can_import"] is True

    # 2. Intervening creation into Elasticsearch
    valid_recs, _, _, _, _, _ = validate_csv_import(es, csv_bytes)
    assert len(valid_recs) == 1
    created_doc = dict(valid_recs[0])
    created_doc["created_at"] = datetime.now(timezone.utc).isoformat()
    created_doc["updated_at"] = created_doc["created_at"]
    try:
        es.create(index=settings.ELASTICSEARCH_INDEX, id=race_id, document=created_doc, refresh=True)

        # 3. Confirm with the CSV previously previewed -> must fail with 409 Conflict due to revalidation
        conf_resp = client.post("/api/artifacts/import", files={"file": ("race.csv", csv_bytes, "text/csv")})
        assert conf_resp.status_code == 409
        detail = conf_resp.json()["detail"]
        assert any("already exists" in e["message"].lower() for e in detail["errors"])
    finally:
        try:
            es.delete(index=settings.ELASTICSEARCH_INDEX, id=race_id, refresh=True)
        except Exception:
            pass


def test_import_quoting_commas_multiline_unicode_bom(client: TestClient) -> None:
    """Verifies correct handling of quoted commas, multiline fields, Unicode, and UTF-8 BOM."""
    es = get_elasticsearch_client()
    test_id = "art-test-rich-content-001"
    row = _make_artifact_dict(
        artifact_id=test_id,
        accession_number="ARC-RICH-001",
        name="Hieroglyphic Stele of Amenhotep 𓂀 — 'Ancient Royal Decree'",
        description="Paragraph 1: Physical description with commas, quotes.\nParagraph 2: Multiline curatorial narrative.",
        medium_materials="Fine limestone, Egyptian blue pigment, Gold leaf",
        tags="hieroglyph, stele, egypt, royal, 18th-dynasty",
    )
    # Generate CSV with UTF-8 BOM
    csv_bytes = _build_csv_bytes([row], bom=True)

    try:
        # 1. Preview works with BOM and rich text
        prev_resp = client.post("/api/artifacts/import/preview", files={"file": ("rich.csv", csv_bytes, "text/csv")})
        assert prev_resp.status_code == 200
        assert prev_resp.json()["can_import"] is True

        # 2. Confirm import
        conf_resp = client.post("/api/artifacts/import", files={"file": ("rich.csv", csv_bytes, "text/csv")})
        assert conf_resp.status_code == 200
        assert conf_resp.json()["success"] is True

        # 3. Verify retrieved artifact in Elasticsearch preserves multiline text and Unicode
        retrieved = client.get(f"/api/artifacts/{test_id}").json()
        assert "𓂀" in retrieved["name"]
        assert "\n" in retrieved["description"]
        assert len(retrieved["medium_materials"]) == 3
        assert "Fine limestone" in retrieved["medium_materials"]
    finally:
        try:
            es.delete(index=settings.ELASTICSEARCH_INDEX, id=test_id, refresh=True)
        except Exception:
            pass


def test_import_formula_injection_defense_roundtrip(client: TestClient) -> None:
    """Verifies that formula injection prefixes are properly unescaped on import and re-escaped on export."""
    es = get_elasticsearch_client()
    test_id = "art-test-formula-roundtrip"
    unique_marker = "UniqueFormulaMarkerToken"
    escaped_formula = "'=SUM(1,2)"
    row = _make_artifact_dict(
        artifact_id=test_id,
        accession_number="ARC-FORMULA-001",
        name=f"Votive Plaque {unique_marker}",
        alternate_names=escaped_formula,
    )
    csv_bytes = _build_csv_bytes([row])

    try:
        # Import
        conf_resp = client.post("/api/artifacts/import", files={"file": ("formula.csv", csv_bytes, "text/csv")})
        assert conf_resp.status_code == 200

        # In Elasticsearch, the formula should be unescaped to '=SUM(1,2)'
        doc = es.get(index=settings.ELASTICSEARCH_INDEX, id=test_id)["_source"]
        assert doc["alternate_names"] == "=SUM(1,2)"

        # When re-exported via /export matching the unique title token, it should be re-escaped with leading apostrophe
        exp_resp = client.get(f"/api/artifacts/export?q={unique_marker}")
        assert exp_resp.status_code == 200
        exp_text = exp_resp.text
        assert "'=SUM(1,2)" in exp_text
    finally:
        try:
            es.delete(index=settings.ELASTICSEARCH_INDEX, id=test_id, refresh=True)
        except Exception:
            pass


def test_import_empty_file_and_header_only(client: TestClient) -> None:
    """Verifies rejection of empty files and CSVs containing only headers without data rows."""
    # Completely empty file
    resp_empty = client.post("/api/artifacts/import/preview", files={"file": ("empty.csv", b"", "text/csv")})
    assert resp_empty.status_code == 200
    assert resp_empty.json()["can_import"] is False
    assert any("empty" in e["message"].lower() for e in resp_empty.json()["errors"])

    # Header-only CSV
    header_only_bytes = _build_csv_bytes([])
    resp_header = client.post("/api/artifacts/import/preview", files={"file": ("header.csv", header_only_bytes, "text/csv")})
    assert resp_header.status_code == 200
    assert resp_header.json()["can_import"] is False
    assert any("no artifact data rows" in e["message"].lower() for e in resp_header.json()["errors"])


def test_import_limits_exceeded(client: TestClient) -> None:
    """Verifies that exceeding the 1,000 row limit is rejected."""
    # Build 1001 rows
    base_row = _make_artifact_dict()
    rows = [dict(base_row, artifact_id=f"art-bulk-{i}", accession_number=f"ARC-B-{i}") for i in range(1001)]
    csv_bytes = _build_csv_bytes(rows)

    resp = client.post("/api/artifacts/import/preview", files={"file": ("huge.csv", csv_bytes, "text/csv")})
    assert resp.status_code == 200
    data = resp.json()
    assert data["can_import"] is False
    assert any("exceeds the maximum limit of 1000" in e["message"].lower() for e in data["errors"])


def test_import_file_size_limit_exceeded(client: TestClient) -> None:
    """Verifies that files exceeding the 5 MB maximum size limit are rejected with HTTP 413."""
    oversized_bytes = b"artifact_id,accession_number\n" + b"x" * (MAX_IMPORT_FILE_SIZE_BYTES + 10)
    resp = client.post("/api/artifacts/import/preview", files={"file": ("too_large.csv", oversized_bytes, "text/csv")})
    assert resp.status_code == 413
    assert "exceeds the maximum size limit" in resp.json()["detail"]


def test_import_invalid_utf8_encoding(client: TestClient) -> None:
    """Verifies that uploaded CSV files containing non-UTF-8 bytes are cleanly rejected."""
    bad_bytes = b"\xff\xfe\x00\x00\x80\x90InvalidBinaryData"
    resp = client.post("/api/artifacts/import/preview", files={"file": ("bad_encoding.csv", bad_bytes, "text/csv")})
    assert resp.status_code == 200
    data = resp.json()
    assert data["can_import"] is False
    assert any("encoding error" in e["message"].lower() for e in data["errors"])


def test_import_partial_bulk_failure_reporting(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    """Verifies that partial bulk failure during Elasticsearch bulk indexing is accurately reported."""
    from elasticsearch import helpers as es_helpers

    def mock_bulk(*args: Any, **kwargs: Any) -> Any:
        return (1, [{"create": {"_id": "art-test-mock-fail", "status": 409, "error": {"reason": "document already exists"}}}] )

    monkeypatch.setattr(es_helpers, "bulk", mock_bulk)

    row1 = _make_artifact_dict(artifact_id="art-test-mock-ok", accession_number="ARC-MOCK-001")
    row2 = _make_artifact_dict(artifact_id="art-test-mock-fail", accession_number="ARC-MOCK-002")
    csv_bytes = _build_csv_bytes([row1, row2])

    resp = client.post("/api/artifacts/import", files={"file": ("mock_bulk.csv", csv_bytes, "text/csv")})
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["imported_count"] == 1
    assert "Partial import completed" in detail["message"]
    assert any("conflict" in e["message"].lower() for e in detail["errors"])


def test_import_export_roundtrip_fidelity(client: TestClient) -> None:
    """Verifies that an exported artifact can be re-imported under a new ID with full fidelity."""
    es = get_elasticsearch_client()
    rt_id = "art-test-roundtrip-fidelity-001"
    rt_acc = "ARC-RT-FIDELITY-001"

    export_resp = client.get("/api/artifacts/export")
    assert export_resp.status_code == 200
    reader = csv.DictReader(io.StringIO(export_resp.text.lstrip("\ufeff")))
    rows = list(reader)
    assert len(rows) >= 30
    orig_row = dict(rows[0])

    rt_name_token = "FidelityRoundtripToken99"
    rt_row = dict(orig_row)
    rt_row["artifact_id"] = rt_id
    rt_row["accession_number"] = rt_acc
    rt_row["name"] = f"Roundtrip Ancient Relic {rt_name_token}"
    csv_bytes = _build_csv_bytes([rt_row])

    try:
        imp_resp = client.post("/api/artifacts/import", files={"file": ("rt.csv", csv_bytes, "text/csv")})
        assert imp_resp.status_code == 200
        assert imp_resp.json()["success"] is True

        reexport_resp = client.get(f"/api/artifacts/export?q={rt_name_token}")
        assert reexport_resp.status_code == 200
        re_reader = csv.DictReader(io.StringIO(reexport_resp.text.lstrip("\ufeff")))
        re_rows = [r for r in re_reader if r["artifact_id"] == rt_id]
        assert len(re_rows) == 1
        fetched = re_rows[0]

        for col in CSV_COLUMNS:
            if col in ("artifact_id", "accession_number", "created_at", "updated_at"):
                continue
            assert fetched[col] == rt_row[col], f"Mismatch on column {col}: {fetched[col]} != {rt_row[col]}"
    finally:
        try:
            es.delete(index=settings.ELASTICSEARCH_INDEX, id=rt_id, refresh=True)
        except Exception:
            pass
