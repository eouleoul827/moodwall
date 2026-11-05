"""Only operator-configured local modules and pinned local artifacts are loaded."""

import hashlib
import importlib
import inspect
import json
from dataclasses import dataclass
from pathlib import Path

from inference.contracts import AnalysisInput, AnalysisResult
from inference.fake import FakeAnalyzer


@dataclass(frozen=True)
class AnalyzerContext:
    """Verified local bundle and deployment scope passed to opted-in factories."""

    manifest_path: Path
    manifest: dict
    environment: str


def load_analyzer(settings):
    if settings.analyzer in {"gemini_mock", "gemini"}:
        from inference.provider_moderation import (
            GeminiModerationAnalyzer,
            ProviderModerationError,
            compute_release_id,
        )

        if settings.analyzer == "gemini_mock" and settings.environment not in {"development", "test"}:
            raise ValueError("gemini_mock analyzer is forbidden outside development/test")
        if settings.analyzer == "gemini" and getattr(settings, "gemini_api_key", None) is None:
            raise ProviderModerationError("MODEL_NOT_READY")
        release_id = compute_release_id(
            inference_policy_version=settings.inference_policy_version,
            lexicon_version=getattr(settings, "lexicon_version", "lexicon-v1"),
            runtime_profile=getattr(settings, "provider_release_parameters", None),
        )
        if (
            settings.model_bundle_version != release_id
            or getattr(settings, "moderation_policy_version", release_id) != release_id
        ):
            raise ValueError("Gemini release does not match deployment")
        emotion_predictor = None
        emotion_manifest = getattr(settings, "emotion_manifest_path", None)
        if emotion_manifest is not None:
            from inference.local_emotion import load_emotion_predictor

            emotion_predictor = load_emotion_predictor(
                emotion_manifest,
                expected_parameters=settings.provider_release_parameters["local_emotion"],
            )
        if settings.analyzer == "gemini":
            from inference.gemini_http import GeminiHTTPTransport

            key = getattr(settings, "gemini_api_key", None)
            if key is None:
                raise ProviderModerationError("MODEL_NOT_READY")
            return GeminiModerationAnalyzer(
                release_id,
                settings.inference_policy_version,
                transport=GeminiHTTPTransport(
                    key,
                    timeout_seconds=settings.provider_timeout_seconds,
                    connect_timeout_seconds=settings.provider_connect_timeout_seconds,
                    max_input_tokens=settings.provider_max_input_tokens,
                ),
                moderation_profile="gemini",
                max_input_tokens=settings.provider_max_input_tokens,
                max_output_tokens=settings.provider_max_output_tokens,
                emotion_predictor=emotion_predictor,
            )
        # Fixture contracts are validated at construction. Do not spend a
        # synthetic provider invocation on the legacy startup warmup message.
        return GeminiModerationAnalyzer(
            release_id, settings.inference_policy_version, emotion_predictor=emotion_predictor
        )
    if settings.analyzer == "fake":
        if settings.environment not in {"development", "test"}:
            raise ValueError("fake analyzer is forbidden outside development/test")
        analyzer = FakeAnalyzer(settings.model_bundle_version, settings.inference_policy_version)
    else:
        module_name, separator, factory_name = settings.analyzer.partition(":")
        if not separator or not module_name or not factory_name.isidentifier():
            raise ValueError("analyzer must be a configured module:factory")
        module = importlib.import_module(module_name)
        factory = getattr(module, factory_name)
        configured_path = getattr(settings, "model_manifest_path", None) or getattr(module, "MANIFEST_PATH", None)
        if not configured_path:
            raise ValueError("configure a model manifest before loading the factory")
        manifest_path = Path(configured_path).resolve(strict=True)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (
            manifest["model_bundle_version"] != settings.model_bundle_version
            or manifest["inference_policy_version"] != settings.inference_policy_version
        ):
            raise ValueError("model manifest does not match deployment")
        artifacts = manifest.get("artifacts", {})
        if not artifacts:
            raise ValueError("model manifest must pin local artifacts")
        for relative, digest in artifacts.items():
            artifact = (manifest_path.parent / relative).resolve(strict=True)
            if not artifact.is_relative_to(manifest_path.parent):
                raise ValueError("artifact escaped bundle directory")
            with artifact.open("rb") as artifact_file:
                actual_digest = hashlib.file_digest(artifact_file, "sha256").hexdigest()
            if actual_digest != digest:
                raise ValueError("artifact hash mismatch")
        # Reviewed local plugin modules must not load models at import time.
        # All local artifact checks finish before the configured factory runs.
        if "context" in inspect.signature(factory).parameters:
            analyzer = factory(context=AnalyzerContext(manifest_path, manifest, settings.environment))
        else:
            # Existing reviewed plugins can keep their parameterless factory.
            analyzer = factory()
    result = AnalysisResult.model_validate(analyzer.analyze(AnalysisInput(text="연결 확인")))
    if (
        result.model_bundle_version != settings.model_bundle_version
        or result.inference_policy_version != settings.inference_policy_version
    ):
        raise ValueError("analyzer output version does not match deployment")
    return analyzer
