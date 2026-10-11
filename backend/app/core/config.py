"""Application configuration module using Pydantic Settings."""

from pathlib import Path
from typing import List
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[3]
_ENV_PATH = _REPO_ROOT / ".env"


class Settings(BaseSettings):
    """Central application settings loaded from environment variables or .env file."""

    model_config = SettingsConfigDict(
        env_file=(_ENV_PATH, ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # General Environment
    ENVIRONMENT: str = Field(default="development", description="Runtime environment")
    LOG_LEVEL: str = Field(default="INFO", description="Logging verbosity")

    # FastAPI Server
    BACKEND_HOST: str = Field(default="127.0.0.1", description="FastAPI host binding")
    BACKEND_PORT: int = Field(default=8000, description="FastAPI port binding")
    CORS_ORIGINS: List[str] = Field(
        default=[
            "http://127.0.0.1:8000",
            "http://localhost:8000",
            "http://127.0.0.1:5500",
            "http://localhost:5500",
            "http://127.0.0.1:3000",
            "http://localhost:3000",
            "http://127.0.0.1:8080",
            "http://localhost:8080",
        ],
        description="Allowed CORS origins",
    )

    # Elasticsearch Connection
    ELASTICSEARCH_HOST: str = Field(default="127.0.0.1", description="Elasticsearch hostname or IP")
    ELASTICSEARCH_PORT: int = Field(default=9200, description="Elasticsearch port")
    ELASTICSEARCH_SCHEME: str = Field(default="http", description="HTTP or HTTPS")
    ELASTIC_USER: str = Field(default="elastic", description="Elasticsearch username")
    ELASTIC_PASSWORD: str = Field(default="", description="Elasticsearch password")
    ELASTICSEARCH_INDEX: str = Field(default="museum_artifacts", description="Primary index name")

    @property
    def elasticsearch_url(self) -> str:
        """Constructs the base URL for Elasticsearch connection."""
        return f"{self.ELASTICSEARCH_SCHEME}://{self.ELASTICSEARCH_HOST}:{self.ELASTICSEARCH_PORT}"


settings = Settings()
