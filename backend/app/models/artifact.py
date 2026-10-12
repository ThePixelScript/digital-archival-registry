"""Pydantic data models for museum artifacts, queries, and responses."""

from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


class Dimensions(BaseModel):
    """Physical dimensions and mass measurements of an archival artifact."""
    model_config = ConfigDict(extra="forbid")
    height_cm: Optional[float] = Field(default=None, ge=0, description="Height in centimeters")
    width_cm: Optional[float] = Field(default=None, ge=0, description="Width in centimeters")
    depth_cm: Optional[float] = Field(default=None, ge=0, description="Depth in centimeters")
    weight_kg: Optional[float] = Field(default=None, ge=0, description="Weight/mass in kilograms")


class ArtifactBase(BaseModel):
    """Base schema containing core artifact attributes matching Elasticsearch mapping."""
    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., min_length=1, max_length=256, description="Primary title or designation of the artifact")
    alternate_names: Optional[str] = Field(default=None, max_length=256, description="Alternative or historical designations")
    category: str = Field(..., min_length=1, max_length=100, description="Standardized archival category")
    historical_period: str = Field(..., min_length=1, max_length=100, description="Chronological historical era")
    origin_culture: str = Field(..., min_length=1, max_length=100, description="Associated civilization or cultural origin")
    origin_location: str = Field(..., min_length=1, max_length=256, description="Geographic location of creation or discovery")
    creation_year_earliest: int = Field(..., ge=-10000, le=2100, description="Earliest estimated creation year (BCE negative, CE positive)")
    creation_year_latest: int = Field(..., ge=-10000, le=2100, description="Latest estimated creation year (BCE negative, CE positive)")
    medium_materials: List[str] = Field(..., min_length=1, description="List of physical materials comprising the object")
    dimensions: Dimensions = Field(default_factory=Dimensions, description="Physical dimensions")
    condition: str = Field(..., min_length=1, max_length=50, description="Conservation condition rating")
    is_on_display: bool = Field(default=True, description="Whether currently on active gallery display")
    storage_location: str = Field(..., min_length=1, max_length=256, description="Gallery vitrine or vault repository location")
    estimated_value_usd: float = Field(..., ge=0, description="Appraised monetary valuation in USD")
    acquisition_date: str = Field(..., description="Date of archival acquisition (YYYY-MM-DD)")
    acquisition_method: str = Field(..., min_length=1, max_length=100, description="Method of acquisition")
    description: str = Field(..., min_length=1, description="Primary physical description")
    historical_context: str = Field(..., min_length=1, description="Historical, archaeological, or cultural context")
    provenance: str = Field(..., min_length=1, description="Recorded chain of custody and ownership history")
    restoration_notes: str = Field(..., min_length=1, description="Conservation treatments and preservation notes")
    tags: List[str] = Field(default_factory=list, description="Descriptive search and taxonomy tags")

    @field_validator("acquisition_date")
    @classmethod
    def validate_date_format(cls, value: str) -> str:
        """Validates that acquisition_date complies with ISO YYYY-MM-DD format."""
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            raise ValueError("acquisition_date must be formatted as YYYY-MM-DD")
        return value


class ArtifactCreate(ArtifactBase):
    """Schema for registering a new artifact record."""
    artifact_id: str = Field(..., min_length=1, max_length=64, description="Unique system document identifier (e.g. art-031)")
    accession_number: str = Field(..., min_length=1, max_length=64, description="Official archival accession number (e.g. ARC-2024-031)")


class ArtifactUpdate(BaseModel):
    """Schema for partial or full updates of an existing artifact."""
    model_config = ConfigDict(extra="forbid")
    name: Optional[str] = Field(default=None, min_length=1, max_length=256)
    alternate_names: Optional[str] = Field(default=None, max_length=256)
    category: Optional[str] = Field(default=None, min_length=1, max_length=100)
    historical_period: Optional[str] = Field(default=None, min_length=1, max_length=100)
    origin_culture: Optional[str] = Field(default=None, min_length=1, max_length=100)
    origin_location: Optional[str] = Field(default=None, min_length=1, max_length=256)
    creation_year_earliest: Optional[int] = Field(default=None, ge=-10000, le=2100)
    creation_year_latest: Optional[int] = Field(default=None, ge=-10000, le=2100)
    medium_materials: Optional[List[str]] = Field(default=None, min_length=1)
    dimensions: Optional[Dimensions] = None
    condition: Optional[str] = Field(default=None, min_length=1, max_length=50)
    is_on_display: Optional[bool] = None
    storage_location: Optional[str] = Field(default=None, min_length=1, max_length=256)
    estimated_value_usd: Optional[float] = Field(default=None, ge=0)
    acquisition_date: Optional[str] = None
    acquisition_method: Optional[str] = Field(default=None, min_length=1, max_length=100)
    description: Optional[str] = Field(default=None, min_length=1)
    historical_context: Optional[str] = Field(default=None, min_length=1)
    provenance: Optional[str] = Field(default=None, min_length=1)
    restoration_notes: Optional[str] = Field(default=None, min_length=1)
    tags: Optional[List[str]] = None

    @field_validator("acquisition_date")
    @classmethod
    def validate_update_date_format(cls, value: Optional[str]) -> Optional[str]:
        if value is not None:
            try:
                datetime.strptime(value, "%Y-%m-%d")
            except ValueError:
                raise ValueError("acquisition_date must be formatted as YYYY-MM-DD")
        return value


class ArtifactResponse(ArtifactBase):
    """Response representation of an artifact stored in Elasticsearch."""
    artifact_id: str
    accession_number: str
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ArtifactSearchResult(ArtifactResponse):
    """Artifact record augmented with search relevance scoring metadata."""
    relevance_score: Optional[float] = Field(default=None, description="BM25 relevance score from Elasticsearch")


class ArtifactListResponse(BaseModel):
    """Paginated list of artifacts."""
    items: List[ArtifactResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class SearchResponse(BaseModel):
    """Response schema for full-text and filtered search queries."""
    items: List[ArtifactSearchResult]
    total: int
    page: int
    page_size: int
    total_pages: int
    query: Optional[str] = None
    fuzzy_applied: bool = False
    took_ms: int


class CollectionStatsResponse(BaseModel):
    """Comprehensive statistical breakdown computed from Elasticsearch aggregations."""
    total_artifacts: int
    categories: Dict[str, int]
    historical_periods: Dict[str, int]
    conditions: Dict[str, int]
    valuation: Dict[str, Any]


class HealthResponse(BaseModel):
    """Health status representation for monitoring service uptime."""
    status: str
    environment: str
    elasticsearch: Dict[str, Any]
