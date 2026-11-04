from __future__ import annotations

from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MOODWALL_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )

    database_url: str = Field(repr=False)
    public_origin: str = "http://localhost:8000"
    # Optional built React application; API-only deployments leave this unset.
    frontend_dist: str | None = None
    board_id: UUID = UUID("10000000-0000-4000-8000-000000000001")
    secret_key: str = Field(min_length=32, repr=False)
    qr_key: str = Field(min_length=32, repr=False)
    qr_kid: str = "v1"
    qr_previous_keys: dict[str, str] = Field(default_factory=dict, repr=False)
    environment: Literal["development", "test", "production"] = "development"
    analyzer: str = "fake"
    model_bundle_version: str = "fake-v1"
    inference_policy_version: str = "inference-v1"
    moderation_policy_version: str = "moderation-v1"
    lexicon_version: str = "lexicon-v1"
    lexicon_path: str | None = None
    model_manifest_path: str | None = None
    worker_warmup_seconds: int = Field(default=180, ge=1, le=600)
    inference_timeout_seconds: int = Field(default=60, ge=1, le=60)
    db_pool_size: int = Field(default=5, ge=1, le=10)
    maintenance_enabled: bool = True
    moderation_backlog_limit: int = Field(default=8, ge=1, le=8)
    moderation_p95_seconds: float = Field(default=10, gt=0, le=60)
    # Shared school Wi-Fi may place many distinct students behind one IP.
    # These are deployment starting values, separate from personal post quotas.
    bootstrap_student_ip_per_minute: int = Field(default=600, ge=1, le=100000)
    bootstrap_display_ip_per_minute: int = Field(default=120, ge=1, le=100000)
    bootstrap_operator_ip_per_minute: int = Field(default=120, ge=1, le=100000)
    read_student_per_minute: int = Field(default=120, ge=1, le=100000)
    read_display_per_minute: int = Field(default=240, ge=1, le=100000)
    read_operator_per_minute: int = Field(default=120, ge=1, le=100000)
    write_per_minute: int = Field(default=120, ge=1, le=100000)

    @model_validator(mode="after")
    def validate_environment(self):
        origin = urlsplit(self.public_origin)
        if origin.scheme not in {"http", "https"} or not origin.hostname:
            raise ValueError("PUBLIC_ORIGIN must be an http(s) origin")
        if origin.path or origin.query or origin.fragment or origin.username or origin.password:
            raise ValueError("PUBLIC_ORIGIN must contain only scheme, host and optional port")
        if self.secret_key == self.qr_key:
            raise ValueError("Session and QR keys must be different")
        if self.qr_kid in self.qr_previous_keys or any(len(v) < 32 for v in self.qr_previous_keys.values()):
            raise ValueError("Invalid QR key map")
        if self.environment == "production":
            if origin.scheme != "https" or self.analyzer == "fake":
                raise ValueError("Production requires HTTPS and a real analyzer")
            if self.secret_key.startswith("change-") or self.qr_key.startswith("change-"):
                raise ValueError("Production placeholder secrets are forbidden")
        return self

    @property
    def secure_cookies(self) -> bool:
        return self.environment != "development"

    def cookie_name(self, role: str) -> str:
        suffix = {"student": "session", "operator": "operator", "display": "display"}[role]
        return ("__Host-" if self.secure_cookies else "") + "mw_" + suffix


@lru_cache
def get_settings() -> Settings:
    return Settings()
