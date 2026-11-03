"""Copy a local student checkpoint into a reproducible, review-only bundle.

The source is never modified. Tensor bytes are copied and hashed in bounded
chunks; importing or running this tool does not import model dependencies.
"""

from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path
from typing import BinaryIO

ARTIFACTS = ("config.json", "tokenizer.json", "tokenizer_config.json", "model.safetensors")
LABELS = {
    str(index): label for index, label in enumerate(("축하", "기쁨", "응원", "설렘", "피곤", "슬픔", "화남", "부적절"))
}
CHUNK_SIZE = 1024 * 1024
JSON_LIMIT = 1024 * 1024


class PreparationError(ValueError):
    """The checkpoint or destination does not meet bundle preparation rules."""


def _no_links(path: Path) -> None:
    for component in (*reversed(path.parents), path):
        if component.is_symlink() or component.is_junction():
            raise PreparationError("Symbolic links and junctions are not supported in bundle paths")


def _paths(source: Path, destination: Path) -> tuple[Path, Path]:
    source = Path(os.path.abspath(source.expanduser()))
    destination = Path(os.path.abspath(destination.expanduser()))
    _no_links(source)
    _no_links(destination)
    source = source.resolve(strict=True)
    destination = destination.resolve(strict=False)
    if not source.is_dir():
        raise PreparationError("Source must be a directory")
    if source == destination or source in destination.parents or destination in source.parents:
        raise PreparationError("Source and destination must be separate, non-nested directories")
    for name in ARTIFACTS:
        _regular_file(source / name)
    return source, destination


def _regular_file(path: Path) -> os.stat_result:
    _no_links(path)
    info = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode):
        raise PreparationError("Each required artifact must be a regular file")
    return info


def _identity(info: os.stat_result) -> tuple[int, ...]:
    identity = info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns
    # Python 3.12 on Windows can expose creation time from stat(), but change
    # time from fstat(). They are not comparable even for an unchanged file.
    return identity if sys.platform == "win32" else (*identity, info.st_ctime_ns)


def _stream(path: Path, writer: BinaryIO | None = None) -> str:
    before = _regular_file(path)
    digest = hashlib.sha256()
    with path.open("rb") as reader:
        if _identity(os.fstat(reader.fileno())) != _identity(before):
            raise PreparationError("An artifact changed before it could be read")
        while chunk := reader.read(CHUNK_SIZE):
            digest.update(chunk)
            if writer is not None:
                writer.write(chunk)
        if _identity(os.fstat(reader.fileno())) != _identity(before):
            raise PreparationError("An artifact changed while it was being read")
    if _identity(_regular_file(path)) != _identity(before):
        raise PreparationError("An artifact changed while it was being read")
    return digest.hexdigest()


def _read_json(path: Path) -> dict:
    _regular_file(path)
    with path.open("rb") as reader:
        payload = reader.read(JSON_LIMIT + 1)
    if len(payload) > JSON_LIMIT:
        raise PreparationError("Configuration JSON exceeds the supported size")
    document = json.loads(payload)
    if not isinstance(document, dict):
        raise PreparationError("Configuration JSON must be an object")
    return document


def _validate_config(directory: Path) -> None:
    config = _read_json(directory / "config.json")
    if config.get("model_type") != "electra" or config.get("architectures") != ["ElectraForSequenceClassification"]:
        raise PreparationError("Expected an ELECTRA sequence classification checkpoint")
    if config.get("id2label") != LABELS or config.get("label2id") != {label: int(key) for key, label in LABELS.items()}:
        raise PreparationError("Checkpoint labels must match the student eight-class order")


def _manifest(hashes: dict[str, str]) -> dict:
    return {
        "model_bundle_version": "student-emotion-v0-" + hashes["model.safetensors"][:12],
        "inference_policy_version": "student-review-only-v1",
        "artifacts": hashes,
        "student_model": {
            "schema_version": "1",
            "kind": "electra-emotion-v0",
            "max_length": 64,
            "cpu_threads": 2,
            "safety_policy": "review-only",
        },
    }


def _identical(destination: Path, manifest: dict) -> bool:
    _no_links(destination)
    if not destination.is_dir():
        return False
    if {entry.name for entry in destination.iterdir()} != {*ARTIFACTS, "manifest.json"}:
        return False
    try:
        if _read_json(destination / "manifest.json") != manifest:
            return False
        return all(_stream(destination / name) == manifest["artifacts"][name] for name in ARTIFACTS)
    except (OSError, ValueError):
        return False


def _publish(staging: Path, destination: Path) -> None:
    """Atomically publish without replacing even an existing empty directory."""
    _no_links(destination)
    if sys.platform == "win32":
        # Unlike POSIX rename(), Windows rename() never replaces a destination.
        os.rename(staging, destination)
        return
    if sys.platform.startswith("linux"):
        library = ctypes.CDLL(None, use_errno=True)
        rename = getattr(library, "renameat2", None)
        if rename is None:
            raise PreparationError("This system does not support no-replace directory publication")
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        if rename(-100, os.fsencode(staging), -100, os.fsencode(destination), 1) != 0:
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code), str(destination))
        return
    raise PreparationError("Bundle preparation currently supports Windows and Linux")


def prepare_bundle(source: Path, destination: Path) -> dict[str, str]:
    source, destination = _paths(source, destination)
    _validate_config(source)
    if destination.exists():
        manifest = _manifest({name: _stream(source / name) for name in ARTIFACTS})
        if not _identical(destination, manifest):
            raise PreparationError("Destination already exists and is not an identical bundle; overwrite is refused")
        return _result(destination, manifest)

    destination.parent.mkdir(parents=True, exist_ok=True)
    _no_links(destination.parent)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.prepare-", dir=destination.parent))
    staging_identity = _identity(staging.stat())[:2]
    try:
        hashes = {}
        for name in ARTIFACTS:
            with (staging / name).open("xb") as writer:
                hashes[name] = _stream(source / name, writer)
                writer.flush()
                os.fsync(writer.fileno())
        _validate_config(staging)
        manifest = _manifest(hashes)
        with (staging / "manifest.json").open("x", encoding="utf-8", newline="\n") as writer:
            json.dump(manifest, writer, ensure_ascii=True, indent=2, sort_keys=True)
            writer.write("\n")
            writer.flush()
            os.fsync(writer.fileno())
        try:
            _publish(staging, destination)
        except OSError as error:
            if error.errno in (errno.EEXIST, errno.ENOTEMPTY) and _identical(destination, manifest):
                return _result(destination, manifest)
            raise
        return _result(destination, manifest)
    finally:
        if staging.exists():
            _no_links(staging)
            if _identity(staging.stat())[:2] != staging_identity:
                raise PreparationError("Staging directory identity changed; cleanup was refused")
            shutil.rmtree(staging)


def _result(destination: Path, manifest: dict) -> dict[str, str]:
    return {
        "manifest_path": str(destination / "manifest.json"),
        "model_bundle_version": manifest["model_bundle_version"],
        "inference_policy_version": manifest["inference_policy_version"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    arguments = parser.parse_args(argv)
    try:
        result = prepare_bundle(arguments.source, arguments.destination)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Model preparation failed: {error}\n")
    print(json.dumps(result, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
