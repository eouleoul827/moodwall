from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
SPEC = importlib.util.spec_from_file_location("moodwall_ai_diagnostic_tests", APP_PATH)
local_api = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = local_api
SPEC.loader.exec_module(local_api)


@pytest.fixture
def api(tmp_path):
    calls = []
    loads = []

    def factory(path):
        loads.append(path)

        def predict(text):
            calls.append(text)
            return SimpleNamespace(label="기쁨", score=0.92345678, inappropriate_score=0.01234567)

        return predict

    application = local_api.create_app(predictor_factory=factory, model_dir=tmp_path)
    assert loads == [], "Importing or creating the app must not load model weights"
    with TestClient(application) as client:
        yield client, calls, loads


def test_startup_loads_once_and_health_is_explicitly_diagnostic(api):
    client, calls, loads = api
    assert len(loads) == 1
    assert client.get("/health").json() == {
        "status": "ok",
        "diagnostic_only": True,
        "public_display_authorized": False,
    }
    for text in ("기분좋아", "축하해"):
        response = client.post("/classify", json={"text": text})
        assert response.status_code == 200
        assert response.json()["diagnostic_only"] is True
        assert response.json()["public_display_authorized"] is False
        assert response.json()["score"] == 0.92345678
        assert text not in response.text
    assert len(loads) == 1 and calls == ["기분좋아", "축하해"]


def test_30_grapheme_input_reaches_predictor_without_strip_or_slice(api):
    client, calls, _loads = api
    original = " 기분좋아" + "👨‍👩‍👧‍👦" * 24 + " "
    assert len(local_api.regex.findall(r"\X", original)) == 30
    assert len(original) > 30
    response = client.post("/classify", json={"text": original})
    assert response.status_code == 200
    assert calls == [original]


@pytest.mark.parametrize("text", ["", "  \t\n", "가" * 31, "👨‍👩‍👧‍👦" * 31, None, 123, ["text"]])
def test_invalid_input_is_rejected_instead_of_rewritten(api, text):
    client, calls, _loads = api
    response = client.post("/classify", json={"text": text})
    assert response.status_code == 422
    assert calls == []
    assert "input" not in response.json()
    if isinstance(text, str) and len(text) >= 31:
        assert text not in response.text


def test_unknown_fields_are_rejected(api):
    client, calls, _loads = api
    assert client.post("/classify", json={"text": "hello", "threshold": 0.1}).status_code == 422
    assert calls == []


def test_actual_utf8_body_size_is_limited_with_and_without_content_length(api):
    client, calls, _loads = api
    prefix = b'{"text":"hello"}'
    exact = prefix + b" " * (local_api.MAX_BODY_BYTES - len(prefix))
    assert client.post("/classify", content=exact, headers={"Content-Type": "application/json"}).status_code == 200
    calls.clear()
    oversized = exact + b" "
    for content in (oversized, iter([oversized[:1000], oversized[1000:]])):
        response = client.post("/classify", content=content, headers={"Content-Type": "application/json"})
        assert response.status_code == 413
    assert client.post("/classify", content=oversized, headers={"Content-Length": "1"}).status_code == 413
    assert calls == []


def test_text_utf8_limit_is_independent_from_wire_json_limit(api):
    client, calls, _loads = api
    exact = "ab" + "\u0301" * 1023
    assert len(exact.encode("utf-8")) == local_api.MAX_TEXT_BYTES
    assert len(local_api.regex.findall(r"\X", exact)) == 2
    assert client.post("/classify", json={"text": exact}).status_code == 200
    assert calls == [exact]
    calls.clear()
    oversized = exact + "\u0301"
    wire = json.dumps({"text": oversized}, ensure_ascii=False).encode("utf-8")
    assert len(wire) < local_api.MAX_BODY_BYTES
    assert client.post("/classify", content=wire, headers={"Content-Type": "application/json"}).status_code == 422
    assert calls == []


@pytest.mark.parametrize("escaped", [b"\\ud800", b"\\udfff"])
def test_unencodable_unicode_is_a_validation_error_without_input_echo(api, escaped):
    client, calls, _loads = api
    response = client.post(
        "/classify", content=b'{"text":"' + escaped + b'"}', headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422
    assert "input" not in response.json()
    assert calls == []


def test_model_path_is_repository_relative_even_when_cwd_changes(tmp_path, monkeypatch):
    monkeypatch.delenv("SLM_MODEL_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    assert local_api.resolve_model_dir() == local_api.REPOSITORY_ROOT / "models" / "student-v0"
    monkeypatch.setenv("SLM_MODEL_DIR", "models/another-bundle")
    assert local_api.resolve_model_dir() == local_api.REPOSITORY_ROOT / "models" / "another-bundle"
    monkeypatch.setenv("SLM_MODEL_DIR", str(tmp_path / "explicit"))
    assert local_api.resolve_model_dir() == tmp_path / "explicit"


def test_missing_bundle_fails_startup_with_actionable_error(tmp_path):
    with pytest.raises(RuntimeError, match="Prepared student model bundle is missing"):
        with TestClient(local_api.create_app(model_dir=tmp_path)):
            pytest.fail("Missing model must not start a healthy API")


def test_inference_exception_is_not_changed_into_an_approval(tmp_path):
    def factory(_path):
        def predict(_text):
            raise RuntimeError("synthetic model failure")

        return predict

    with TestClient(local_api.create_app(predictor_factory=factory, model_dir=tmp_path)) as client:
        with pytest.raises(RuntimeError, match="synthetic model failure"):
            client.post("/classify", json={"text": "hello"})


def test_import_does_not_require_or_load_torch_and_transformers():
    script = (
        "import importlib.util,sys; "
        f"spec=importlib.util.spec_from_file_location('local_diagnostic',{str(APP_PATH)!r}); "
        "module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); "
        "assert 'torch' not in sys.modules; assert 'transformers' not in sys.modules"
    )
    subprocess.run([sys.executable, "-c", script], check=True, capture_output=True, text=True, timeout=20)


def test_launcher_is_loopback_only_on_8001_without_automatic_restart():
    launcher = (APP_PATH.parent / "start_slm.bat").read_text(encoding="utf-8")
    assert "--host 127.0.0.1 --port 8001 --no-access-log" in launcher
    assert "--port 8000" not in launcher
    assert "goto loop" not in launcher.lower()
