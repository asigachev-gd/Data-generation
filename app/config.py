"""Typed application configuration loaded from the environment."""

from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings. Vertex AI uses Application Default Credentials."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    google_cloud_project: str = Field(min_length=1)
    google_cloud_location: str = Field(default="us-central1", min_length=1)
    vertex_ai_enabled: bool
    gemini_model: str = Field(default="gemini-2.0-flash-001", min_length=1)

    postgres_host: str = Field(default="localhost", min_length=1)
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    postgres_db: str = Field(min_length=1)
    postgres_user: str = Field(min_length=1)
    postgres_password: SecretStr = Field(min_length=1)

    langfuse_public_key: SecretStr | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_host: str = "https://cloud.langfuse.com"

    @field_validator("google_cloud_project")
    @classmethod
    def reject_placeholder_project(cls, value: str) -> str:
        if value == "your-gcp-project-id":
            raise ValueError("must be set to a real GCP project ID")
        return value

    @property
    def database_dsn(self) -> str:
        """Return a connection string without exposing it in logs or errors."""
        return (
            f"host={self.postgres_host} port={self.postgres_port} dbname={self.postgres_db} "
            f"user={self.postgres_user} password={self.postgres_password.get_secret_value()}"
        )

    @property
    def langfuse_enabled(self) -> bool:
        return bool(self.langfuse_public_key and self.langfuse_secret_key)


@lru_cache
def get_settings() -> Settings:
    """Load and cache validated process configuration."""
    return Settings()
