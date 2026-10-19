"""Tests for static frontend asset mounting and serving."""

from fastapi.testclient import TestClient


def test_root_redirects_to_frontend(client: TestClient) -> None:
    """Verifies that the root endpoint redirects to the mounted frontend application."""
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers.get("location") == "/app/"

    # Verify that following redirects leads to the 200 OK frontend entry point
    follow_response = client.get("/", follow_redirects=True)
    assert follow_response.status_code == 200
    assert "text/html" in follow_response.headers.get("content-type", "")
    assert "Digital Archival Registry" in follow_response.text


def test_frontend_index_html_served(client: TestClient) -> None:
    """Verifies that the HTML entry point is properly served under /app/."""
    response = client.get("/app/")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    content = response.text
    assert "<!DOCTYPE html>" in content
    assert "Digital Archival Registry" in content
    assert "id=\"search-input\"" in content
    assert "id=\"artifacts-grid\"" in content
    assert "id=\"detail-modal\"" in content
    assert "id=\"form-modal\"" in content
    assert "id=\"delete-modal\"" in content
    assert "id=\"analytics-panel\"" in content


def test_frontend_css_served(client: TestClient) -> None:
    """Verifies that stylesheet assets are served with proper content-type."""
    response = client.get("/app/css/styles.css")
    assert response.status_code == 200
    assert "text/css" in response.headers.get("content-type", "")
    assert "--color-bg-canvas" in response.text


def test_frontend_js_served(client: TestClient) -> None:
    """Verifies that JavaScript modules are served with proper content-type."""
    response = client.get("/app/js/app.js")
    assert response.status_code == 200
    content_type = response.headers.get("content-type", "")
    assert any(mime in content_type for mime in ("javascript", "application/x-javascript", "text/javascript"))
    assert "DOMContentLoaded" in response.text
    assert "API_BASE_URL" in response.text


def test_frontend_favicon_svg_served(client: TestClient) -> None:
    """Verifies that the SVG favicon is directly accessible in the mounted static frontend directory."""
    response = client.get("/app/favicon.svg")
    assert response.status_code == 200
    assert "image/svg+xml" in response.headers.get("content-type", "")
    assert "<svg" in response.text


def test_root_favicon_ico_served(client: TestClient) -> None:
    """Verifies that the /favicon.ico route serves the local SVG icon to prevent 404 browser errors."""
    response = client.get("/favicon.ico")
    assert response.status_code == 200
    assert "image/svg+xml" in response.headers.get("content-type", "")
    assert "<svg" in response.text


def test_root_favicon_svg_served(client: TestClient) -> None:
    """Verifies that the root /favicon.svg route serves the local SVG icon."""
    response = client.get("/favicon.svg")
    assert response.status_code == 200
    assert "image/svg+xml" in response.headers.get("content-type", "")
    assert "<svg" in response.text


def test_frontend_demo_notice_present(client: TestClient) -> None:
    """Verifies that the HTML document includes explicit notice of fictional demonstration data."""
    response = client.get("/app/")
    assert response.status_code == 200
    assert "Demonstration collection: records are fictional and intended for software testing and demonstration." in response.text
