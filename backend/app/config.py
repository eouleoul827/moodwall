from __future__ import annotations

import hashlib
from copy import deepcopy
from datetime import UTC, date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import AliasChoices, Field, PrivateAttr, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def gemini_tariff(day: date) -> tuple[date, date, int, int]:
    """Published standard-tier bands verified 2026-11-04; never extrapolate."""
    bands = (
        (date(2026, 11, 4), date(2026, 12, 31), 750000, 3750000),
        (date(2027, 1, 1), date(2027, 12, 31), 1500000, 7500000),
    )
    for band in bands:
        if band[0] <= day <= band[1]:
            return band
    raise ValueError("No verified Gemini tariff for the current date")


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
    moderation_profile: Literal["legacy", "gemini_mock", "gemini"] = "legacy"
    gemini_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("MOODWALL_GEMINI_API_KEY", "GEMINI_API_KEY", "gemini_api_key"),
        repr=False,
        exclude=True,
    )
    auto_strikes: bool = True
    model_bundle_version: str = "fake-v1"
    inference_policy_version: str = "inference-v1"
    moderation_policy_version: str = "moderation-v1"
    lexicon_version: str = "lexicon-v1"
    lexicon_path: str | None = None
    model_manifest_path: str | None = None
    # Optional local presentation classifier; Gemini remains the safety gate.
    emotion_manifest_path: str | None = None
    _emotion_release: dict | None = PrivateAttr(default=None)
    worker_warmup_seconds: int = Field(default=180, ge=1, le=600)
    inference_timeout_seconds: int = Field(default=60, ge=1, le=60)
    db_pool_size: int = Field(default=5, ge=1, le=10)
    # Playback is fixed at fifteen seconds. Removed PLAYBACK_* environment
    # variables remain harmless because obsolete settings are ignored.
    maintenance_enabled: bool = True
    moderation_backlog_limit: int = Field(default=8, ge=1, le=8)
    moderation_p95_seconds: float = Field(default=10, gt=0, le=60)
    provider_timeout_seconds: float = Field(default=8, gt=0, le=8)
    provider_connect_timeout_seconds: float = Field(default=2, gt=0, le=2)
    provider_wrapper_timeout_seconds: float = Field(default=12, gt=0, le=12)
    provider_max_input_tokens: int = Field(default=6144, ge=1, le=16384)
    provider_max_output_tokens: int = Field(default=512, ge=1, le=512)
    provider_rpm: int = Field(default=6, ge=1, le=10000)
    provider_input_tpm: int = Field(default=40000, ge=1, le=100000000)
    provider_rpd: int = Field(default=1000, ge=1, le=10000000)
    provider_daily_budget_micro_usd: int = Field(default=3000000, ge=1, le=100000000000)
    # Synthetic tariff for exercising integer accounting offline; no billing claim.
    provider_input_micro_usd_per_million: int = Field(default=500000, ge=1)
    provider_output_micro_usd_per_million: int = Field(default=3000000, ge=1)
    provider_tariff_valid_from: date = date(2026, 11, 4)
    provider_tariff_valid_until: date = date(2026, 12, 31)
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
        if self.emotion_manifest_path is not None:
            if self.moderation_profile not in {"gemini", "gemini_mock"}:
                raise ValueError("Local presentation emotions require Gemini moderation")
            from inference.local_emotion import verify_emotion_bundle

            self._emotion_release = verify_emotion_bundle(self.emotion_manifest_path).release_parameters
        origin = urlsplit(self.public_origin)
        if origin.scheme not in {"http", "https"} or not origin.hostname:
            raise ValueError("PUBLIC_ORIGIN must be an http(s) origin")
        if origin.path or origin.query or origin.fragment or origin.username or origin.password:
            raise ValueError("PUBLIC_ORIGIN must contain only scheme, host and optional port")
        if self.secret_key == self.qr_key:
            raise ValueError("Session and QR keys must be different")
        if self.qr_kid in self.qr_previous_keys or any(len(v) < 32 for v in self.qr_previous_keys.values()):
            raise ValueError("Invalid QR key map")
        if self.moderation_profile != "legacy":
            from inference.provider_moderation import compute_release_id

            if self.moderation_profile == "gemini_mock" and self.environment == "production":
                raise ValueError("Offline moderation mock is forbidden in production")
            if self.moderation_profile == "gemini":
                if self.gemini_api_key is not None:
                    normalized_key = self.gemini_api_key.get_secret_value().strip()
                    self.gemini_api_key = SecretStr(normalized_key) if normalized_key else None
                band = gemini_tariff(datetime.now(UTC).date())
                for name, expected_value in zip(
                    (
                        "provider_tariff_valid_from",
                        "provider_tariff_valid_until",
                        "provider_input_micro_usd_per_million",
                        "provider_output_micro_usd_per_million",
                    ),
                    band,
                    strict=True,
                ):
                    if name in self.model_fields_set and getattr(self, name) != expected_value:
                        raise ValueError("Gemini tariff must match the current published band")
                    setattr(self, name, expected_value)
                if "provider_max_input_tokens" not in self.model_fields_set:
                    self.provider_max_input_tokens = 16384
                if "provider_input_tpm" not in self.model_fields_set:
                    self.provider_input_tpm = 200000
            if self.analyzer == "fake":
                self.analyzer = self.moderation_profile
            if self.analyzer != self.moderation_profile:
                raise ValueError("Moderation profile and analyzer must match")
            if "auto_strikes" in self.model_fields_set and self.auto_strikes:
                raise ValueError("Provider moderation cannot apply automatic strikes")
            self.auto_strikes = False
            if "inference_timeout_seconds" not in self.model_fields_set:
                self.inference_timeout_seconds = 15
            if "moderation_p95_seconds" not in self.model_fields_set:
                self.moderation_p95_seconds = 8
            if "inference_policy_version" not in self.model_fields_set:
                self.inference_policy_version = "gemini-inference-v1"
            if self.inference_timeout_seconds < self.provider_wrapper_timeout_seconds + 2:
                raise ValueError("Provider wrapper requires two seconds of finalization reserve")
            if self.provider_timeout_seconds + 2 > self.provider_wrapper_timeout_seconds:
                raise ValueError("Provider call exceeds wrapper time budget")
            expected = compute_release_id(
                inference_policy_version=self.inference_policy_version,
                lexicon_version=self.lexicon_version,
                runtime_profile=self.provider_release_parameters,
            )
            if self.model_bundle_version == "fake-v1":
                self.model_bundle_version = expected
            if self.moderation_policy_version == "moderation-v1":
                self.moderation_policy_version = expected
            if self.model_bundle_version != expected or self.moderation_policy_version != expected:
                raise ValueError("Provider release configuration does not match its immutable hash")
        elif self.analyzer in {"gemini_mock", "gemini"}:
            raise ValueError("Provider analyzer requires its matching moderation profile")
        if self.environment == "production":
            if not self.maintenance_enabled:
                raise ValueError("Production requires the internal maintenance loops")
            if origin.scheme != "https" or self.analyzer == "fake":
                raise ValueError("Production requires HTTPS and a real analyzer")
            if self.secret_key.startswith("change-") or self.qr_key.startswith("change-"):
                raise ValueError("Production placeholder secrets are forbidden")
        return self

    @property
    def provider_release_parameters(self) -> dict:
        names = (
            "moderation_profile",
            "provider_timeout_seconds",
            "provider_connect_timeout_seconds",
            "provider_wrapper_timeout_seconds",
            "inference_timeout_seconds",
            "provider_max_input_tokens",
            "provider_max_output_tokens",
            "provider_rpm",
            "provider_input_tpm",
            "provider_rpd",
            "provider_daily_budget_micro_usd",
            "provider_input_micro_usd_per_million",
            "provider_output_micro_usd_per_million",
            "auto_strikes",
        )
        result = {name: getattr(self, name) for name in names}
        if self._emotion_release is not None:
            # Verified at Settings creation, not once per HTTP request. The
            # worker rechecks before loading; filesystem paths are not identity.
            result["local_emotion"] = deepcopy(self._emotion_release)
        if self.moderation_profile == "gemini":
            result["provider_tariff_valid_from"] = self.provider_tariff_valid_from.isoformat()
            result["provider_tariff_valid_until"] = self.provider_tariff_valid_until.isoformat()
            result["provider_http_calls_per_dispatch"] = 2
        lexicon = Path(self.lexicon_path) if self.lexicon_path else Path(__file__).parent / "resources" / "lexicon.json"
        try:
            result["lexicon_sha256"] = hashlib.sha256(lexicon.read_bytes()).hexdigest()
            result["rule_filter_sha256"] = hashlib.sha256(
                (Path(__file__).parent / "profanity.py").read_bytes()
            ).hexdigest()
        except OSError:
            raise ValueError("Provider rule artifacts are unavailable") from None
        return result

    @property
    def secure_cookies(self) -> bool:
        return self.environment != "development"

    def cookie_name(self, role: str) -> str:
        suffix = {"student": "session", "operator": "operator", "display": "display"}[role]
        return ("__Host-" if self.secure_cookies else "") + "mw_" + suffix


@lru_cache
def get_settings() -> Settings:
    return Settings()
