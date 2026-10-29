"""Only operator-configured local modules and pinned local artifacts are loaded."""

import hashlib
import importlib
import json
from pathlib import Path

from inference.contracts import AnalysisInput, AnalysisResult
from inference.fake import FakeAnalyzer


def load_analyzer(settings):
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
        analyzer = factory()
    result = AnalysisResult.model_validate(analyzer.analyze(AnalysisInput(text="연결 확인")))
    if (
        result.model_bundle_version != settings.model_bundle_version
        or result.inference_policy_version != settings.inference_policy_version
    ):
        raise ValueError("analyzer output version does not match deployment")
    return analyzer
