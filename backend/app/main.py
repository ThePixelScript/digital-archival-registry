"""Main FastAPI application entry point for the Digital Archival Registry & Museum Collection Tracker."""

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator
from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from app.api.routes import api_router
from app.core.config import settings
from app.core.elasticsearch import close_elasticsearch_client, get_elasticsearch_client


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan context manager for initializing and tearing down resources."""
    # Ensure Elasticsearch client is initialized and reachable on startup
    get_elasticsearch_client()
    yield
    # Clean up client resources on shutdown
    close_elasticsearch_client()


app = FastAPI(
    title="Digital Archival Registry & Museum Collection Tracker",
    description=(
        "A searchable digital archive exposing Elasticsearch 8.x document storage, "
        "relevance-ranked full-text discovery, typo tolerance, multi-attribute filtering, "
        "and real-time aggregations through a clean, documented REST API."
    ),
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# Configure Cross-Origin Resource Sharing (CORS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register API routes under /api
app.include_router(api_router, prefix="/api")

# Mount static frontend directory under /app
_FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"
_FAVICON_PATH = _FRONTEND_DIR / "favicon.svg"
if _FRONTEND_DIR.is_dir():
    app.mount("/app", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")


@app.get("/favicon.ico", include_in_schema=False)
@app.get("/favicon.svg", include_in_schema=False)
def favicon() -> Response:
    """Serves the SVG favicon at root for browsers requesting /favicon.ico or /favicon.svg."""
    if _FAVICON_PATH.is_file():
        return FileResponse(_FAVICON_PATH, media_type="image/svg+xml")
    return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"detail": "Not found"})


@app.get("/", tags=["Root"], summary="Root endpoint providing API information")
def root_info() -> dict:
    """Returns application name, version, and documentation links."""
    return {
        "name": "Digital Archival Registry & Museum Collection Tracker API",
        "version": "0.1.0",
        "environment": settings.ENVIRONMENT,
        "docs_url": "/docs",
        "health_url": "/api/health",
        "frontend_url": "/app/",
    }
