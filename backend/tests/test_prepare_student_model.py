from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import prepare_student_model as preparation


@pytest.fixture
def checkpoint(tmp_path):
    source = tmp_path / "checkpoint"
    source.mkdir()
    config = {
        "model_type": "electra",
        "architectures": ["ElectraForSequenceClassification"],
        "id2label": preparation.LABELS,
        "label2id": {label: int(key) for key, label in preparation.LABELS.items()},
    }
    (source / "config.json").write_text(json.dumps(config), encoding="utf-8")
    (source / "tokenizer.json").write_text('{"version":"1.0"}', encoding="utf-8")
    (source / "tokenizer_config.json").write_text("{}", encoding="utf-8")
    (source / "model.safetensors").write_bytes(b"synthetic weights\x00" * 97)
    return source


def _snapshot(directory):
    return {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in directory.iterdir()}


def test_bundle_has_reproducible_hashes_and_preserves_source(checkpoint, tmp_path):
    (checkpoint / "do-not-copy.txt").write_text("not an artifact", encoding="utf-8")
    before = _snapshot(checkpoint)
    destination = tmp_path / "bundles" / "student-v0"

    result = preparation.prepare_bundle(checkpoint, destination)

    assert _snapshot(checkpoint) == before
    assert {path.name for path in destination.iterdir()} == {*preparation.ARTIFACTS, "manifest.json"}
    manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
    hashes = {name: hashlib.sha256(before[name][0]).hexdigest() for name in preparation.ARTIFACTS}
    assert manifest["artifacts"] == hashes
    canonical = json.dumps(hashes, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    bundle_digest = hashlib.sha256(canonical).hexdigest()
    assert manifest["model_bundle_version"] == "student-emotion-v0-bundle-" + bundle_digest[:16]
    assert manifest["bundle_identity"] == {
        "schema_version": "1",
        "algorithm": "sha256",
        "canonicalization": "sorted-artifact-sha256-json-v1",
        "digest": bundle_digest,
    }
    assert manifest["inference_policy_version"] == "student-review-only-v1"
    assert manifest["student_model"] == {
        "schema_version": "1",
        "kind": "electra-emotion-v0",
        "max_length": 64,
        "cpu_threads": 2,
        "safety_policy": "review-only",
    }
    assert Path(result["manifest_path"]) == destination / "manifest.json"
    assert Path(result["manifest_path"]).is_absolute()


def test_identical_bundle_is_idempotent_without_rewriting(checkpoint, tmp_path):
    destination = tmp_path / "student-v0"
    first = preparation.prepare_bundle(checkpoint, destination)
    before = _snapshot(destination)
    assert preparation.prepare_bundle(checkpoint, destination) == first
    assert _snapshot(destination) == before


@pytest.mark.parametrize("changed", preparation.ARTIFACTS)
def test_each_artifact_changes_the_bundle_version_even_when_weights_do_not(checkpoint, tmp_path, changed):
    first = preparation.prepare_bundle(checkpoint, tmp_path / "first")
    with (checkpoint / changed).open("ab") as writer:
        writer.write(b"\n")
    second = preparation.prepare_bundle(checkpoint, tmp_path / "second")
    assert first["model_bundle_version"] != second["model_bundle_version"]
    first_manifest = json.loads(Path(first["manifest_path"]).read_text(encoding="utf-8"))
    second_manifest = json.loads(Path(second["manifest_path"]).read_text(encoding="utf-8"))
    if changed != "model.safetensors":
        assert first_manifest["artifacts"]["model.safetensors"] == second_manifest["artifacts"]["model.safetensors"]


def test_bundle_identity_does_not_depend_on_dictionary_order(checkpoint):
    hashes = {name: hashlib.sha256((checkpoint / name).read_bytes()).hexdigest() for name in preparation.ARTIFACTS}
    reordered = dict(reversed(list(hashes.items())))
    assert preparation._manifest(hashes) == preparation._manifest(reordered)


def _write_legacy_manifest(destination):
    manifest_path = destination / "manifest.json"
    current = json.loads(manifest_path.read_text(encoding="utf-8"))
    legacy = {
        "model_bundle_version": "student-emotion-v0-" + current["artifacts"]["model.safetensors"][:12],
        "inference_policy_version": "student-review-only-v1",
        "artifacts": current["artifacts"],
        "student_model": {
            "schema_version": "1",
            "kind": "electra-emotion-v0",
            "max_length": 64,
            "cpu_threads": 2,
            "safety_policy": "review-only",
        },
    }
    manifest_path.write_text(json.dumps(legacy, indent=2), encoding="utf-8")
    return legacy


def test_legacy_bundle_is_returned_without_upgrading_or_rewriting_its_manifest(checkpoint, tmp_path):
    destination = tmp_path / "legacy"
    current = preparation.prepare_bundle(checkpoint, destination)
    legacy = _write_legacy_manifest(destination)
    before = _snapshot(destination)

    result = preparation.prepare_bundle(checkpoint, destination)

    assert result["manifest_path"] == current["manifest_path"]
    assert result["model_bundle_version"] == legacy["model_bundle_version"]
    assert result["inference_policy_version"] == legacy["inference_policy_version"]
    assert _snapshot(destination) == before


@pytest.mark.parametrize(
    "mutation",
    ["bundle_version", "policy_version", "extra", "missing", "student_extra", "student_policy", "float", "hash"],
)
def test_legacy_compatibility_does_not_accept_edited_metadata(checkpoint, tmp_path, mutation):
    destination = tmp_path / "legacy"
    preparation.prepare_bundle(checkpoint, destination)
    legacy = _write_legacy_manifest(destination)
    if mutation == "bundle_version":
        legacy["model_bundle_version"] += "-edited"
    elif mutation == "policy_version":
        legacy["inference_policy_version"] = "allow-v1"
    elif mutation == "extra":
        legacy["unknown"] = "field"
    elif mutation == "missing":
        del legacy["student_model"]
    elif mutation == "student_extra":
        legacy["student_model"]["unsafe_threshold"] = 0.2
    elif mutation == "student_policy":
        legacy["student_model"]["safety_policy"] = "allow"
    elif mutation == "float":
        legacy["student_model"]["cpu_threads"] = 2.0
    else:
        legacy["artifacts"]["tokenizer.json"] = "0" * 64
    (destination / "manifest.json").write_text(json.dumps(legacy), encoding="utf-8")
    before = _snapshot(destination)

    with pytest.raises(preparation.PreparationError, match="overwrite is refused"):
        preparation.prepare_bundle(checkpoint, destination)

    assert _snapshot(destination) == before


def test_legacy_bundle_with_edited_artifact_is_not_overwritten(checkpoint, tmp_path):
    destination = tmp_path / "legacy"
    preparation.prepare_bundle(checkpoint, destination)
    _write_legacy_manifest(destination)
    (destination / "tokenizer_config.json").write_text('{"changed":true}', encoding="utf-8")
    before = _snapshot(destination)
    with pytest.raises(preparation.PreparationError, match="overwrite is refused"):
        preparation.prepare_bundle(checkpoint, destination)
    assert _snapshot(destination) == before


def test_legacy_manifest_with_duplicate_metadata_fields_is_not_accepted(checkpoint, tmp_path):
    destination = tmp_path / "legacy"
    preparation.prepare_bundle(checkpoint, destination)
    legacy = _write_legacy_manifest(destination)
    manifest_path = destination / "manifest.json"
    document = json.dumps(legacy)
    manifest_path.write_text('{"inference_policy_version":"edited",' + document[1:], encoding="utf-8")
    before = _snapshot(destination)
    with pytest.raises(preparation.PreparationError, match="overwrite is refused"):
        preparation.prepare_bundle(checkpoint, destination)
    assert _snapshot(destination) == before


def test_racing_legacy_publication_returns_the_preserved_legacy_identity(checkpoint, tmp_path, monkeypatch):
    destination = tmp_path / "legacy"
    published = []

    def publish_legacy(staging, target):
        shutil.copytree(staging, target)
        published.append(_write_legacy_manifest(target))
        raise FileExistsError(preparation.errno.EEXIST, "another preparer published a legacy bundle")

    monkeypatch.setattr(preparation, "_publish", publish_legacy)
    result = preparation.prepare_bundle(checkpoint, destination)
    assert result["model_bundle_version"] == published[0]["model_bundle_version"]
    assert json.loads((destination / "manifest.json").read_text(encoding="utf-8")) == published[0]
    assert not list(tmp_path.glob(".legacy.prepare-*"))


@pytest.mark.parametrize("platform,ctime_is_comparable", [("win32", False), ("linux", True)])
def test_file_identity_handles_windows_stat_fstat_ctime_difference(monkeypatch, platform, ctime_is_comparable):
    monkeypatch.setattr(preparation.sys, "platform", platform)
    values = {"st_dev": 1, "st_ino": 2, "st_size": 123, "st_mtime_ns": 456}
    path_stat = SimpleNamespace(**values, st_ctime_ns=100)
    descriptor_stat = SimpleNamespace(**values, st_ctime_ns=200)
    assert (preparation._identity(path_stat) != preparation._identity(descriptor_stat)) == ctime_is_comparable
    changed = SimpleNamespace(**{**values, "st_mtime_ns": 789}, st_ctime_ns=200)
    assert preparation._identity(path_stat) != preparation._identity(changed)


@pytest.mark.parametrize("existing", ["empty", "file", "different", "tampered", "extra", "manifest"])
def test_existing_destination_is_never_overwritten(checkpoint, tmp_path, existing):
    destination = tmp_path / "student-v0"
    if existing == "file":
        destination.write_bytes(b"keep this file")
        before = destination.read_bytes()
    else:
        if existing in ("tampered", "extra", "manifest"):
            preparation.prepare_bundle(checkpoint, destination)
        else:
            destination.mkdir()
        if existing == "different":
            (destination / "important.txt").write_text("keep", encoding="utf-8")
        elif existing == "tampered":
            (destination / "model.safetensors").write_bytes(b"other weights")
        elif existing == "extra":
            (destination / "important.txt").write_text("keep", encoding="utf-8")
        elif existing == "manifest":
            (destination / "manifest.json").write_text("{}", encoding="utf-8")
        before = _snapshot(destination)

    with pytest.raises(preparation.PreparationError, match="overwrite is refused"):
        preparation.prepare_bundle(checkpoint, destination)

    assert (destination.read_bytes() if destination.is_file() else _snapshot(destination)) == before
    assert not list(tmp_path.glob(".student-v0.prepare-*"))


@pytest.mark.parametrize("relationship", ["same", "inside_source", "contains_source"])
def test_overlapping_paths_are_rejected(checkpoint, relationship):
    destination = {
        "same": checkpoint,
        "inside_source": checkpoint / "bundle",
        "contains_source": checkpoint.parent,
    }[relationship]
    before = _snapshot(checkpoint)
    with pytest.raises(preparation.PreparationError, match="non-nested"):
        preparation.prepare_bundle(checkpoint, destination)
    assert _snapshot(checkpoint) == before


@pytest.mark.parametrize("missing", preparation.ARTIFACTS)
def test_missing_artifact_does_not_create_destination(checkpoint, tmp_path, missing):
    (checkpoint / missing).unlink()
    destination = tmp_path / "student-v0"
    with pytest.raises(FileNotFoundError):
        preparation.prepare_bundle(checkpoint, destination)
    assert not destination.exists()


@pytest.mark.parametrize("invalid", ["architecture", "labels", "inverse"])
def test_wrong_model_contract_is_rejected(checkpoint, tmp_path, invalid):
    path = checkpoint / "config.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    if invalid == "architecture":
        config["model_type"] = "other"
    elif invalid == "labels":
        config["id2label"]["7"] = "NEUTRAL"
    else:
        config["label2id"] = {}
    path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(preparation.PreparationError):
        preparation.prepare_bundle(checkpoint, tmp_path / "student-v0")
    assert not (tmp_path / "student-v0").exists()


def test_failed_copy_removes_only_owned_staging(checkpoint, tmp_path, monkeypatch):
    unrelated = tmp_path / ".student-v0.prepare-unrelated"
    unrelated.mkdir()
    (unrelated / "keep").write_bytes(b"keep")
    source_before = _snapshot(checkpoint)
    real_stream = preparation._stream

    def failing_stream(path, writer=None):
        if path.name == "model.safetensors":
            if writer is not None:
                writer.write(b"partial")
            raise OSError("simulated disk failure")
        return real_stream(path, writer)

    monkeypatch.setattr(preparation, "_stream", failing_stream)
    destination = tmp_path / "student-v0"
    with pytest.raises(OSError, match="disk failure"):
        preparation.prepare_bundle(checkpoint, destination)
    assert not destination.exists()
    assert list(tmp_path.glob(".student-v0.prepare-*")) == [unrelated]
    assert (unrelated / "keep").read_bytes() == b"keep"
    assert _snapshot(checkpoint) == source_before


def test_publication_race_does_not_replace_an_existing_empty_directory(checkpoint, tmp_path, monkeypatch):
    destination = tmp_path / "student-v0"
    real_publish = preparation._publish
    identity = []

    def raced_publish(staging, target):
        target.mkdir()
        identity.append(target.stat().st_ino)
        real_publish(staging, target)

    monkeypatch.setattr(preparation, "_publish", raced_publish)
    with pytest.raises(OSError):
        preparation.prepare_bundle(checkpoint, destination)
    assert destination.stat().st_ino == identity[0]
    assert list(destination.iterdir()) == []
    assert not list(tmp_path.glob(".student-v0.prepare-*"))


@pytest.mark.parametrize("target_kind", ["source", "artifact", "destination_parent"])
def test_symlinks_cannot_redirect_bundle_paths(checkpoint, tmp_path, target_kind):
    outside = tmp_path / "outside"
    outside.mkdir()
    destination = tmp_path / "student-v0"
    source = checkpoint
    try:
        if target_kind == "source":
            source = tmp_path / "source-link"
            source.symlink_to(checkpoint, target_is_directory=True)
        elif target_kind == "artifact":
            (outside / "weights").write_bytes(b"outside weights")
            (checkpoint / "model.safetensors").unlink()
            (checkpoint / "model.safetensors").symlink_to(outside / "weights")
        else:
            parent = tmp_path / "destination-link"
            parent.symlink_to(outside, target_is_directory=True)
            destination = parent / "student-v0"
    except OSError:
        pytest.skip("This Windows account cannot create symbolic links")
    with pytest.raises(preparation.PreparationError, match="links and junctions"):
        preparation.prepare_bundle(source, destination)
    assert not destination.exists()
    assert not (outside / "student-v0").exists()


def test_cli_prints_only_manifest_identity(checkpoint, tmp_path, capsys):
    destination = tmp_path / "student-v0"
    assert preparation.main(["--source", str(checkpoint), "--destination", str(destination)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert set(output) == {"manifest_path", "model_bundle_version", "inference_policy_version"}
    assert "synthetic weights" not in json.dumps(output)
