"""The validation manifest: what the image contains, by hash, written during the build.

The manifest is the image's own account of its contents. Labels are checked
against it after the build, and `dvgate selfcheck` re-hashes the files against
it whenever the container starts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .common import Check, ConfigError, gate_status, sha256_file
from .contracts import DataContract, ModelContract
from .labels import LabelStandard

MANIFEST_VERSION = 1


def describe_inputs(
    record: dict[str, Any],
    dataset_path: Path,
    data_contract: DataContract,
    model_contract: ModelContract,
    model_dir: Path,
    standard: LabelStandard,
) -> dict[str, Any]:
    """Identity of every input, computed from the bytes on disk."""
    model_file = model_dir / "model.json"
    if not model_file.is_file():
        raise ConfigError(f"no model.json in {model_dir}")
    model = json.loads(model_file.read_text(encoding="utf-8"))
    return {
        "dataset": {
            "name": record["name"],
            "version": record["version"],
            "sha256": sha256_file(dataset_path),
            "source": record["source"],
            "revision": record["revision"],
            "licence": record["licence"],
        },
        "schema": {
            "name": data_contract.name,
            "version": data_contract.version,
            "sha256": data_contract.sha256,
        },
        "model": {
            "name": str(model.get("name")),
            "version": str(model.get("version")),
            "sha256": sha256_file(model_file),
        },
        "label_standard": {"name": standard.name, "version": standard.version},
    }


def _lookup(document: dict[str, Any], dotted: str) -> Any:
    node: Any = document
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def verify_inputs(
    identity: dict[str, Any],
    record: dict[str, Any],
    model_contract: ModelContract,
    expectations: dict[str, str],
) -> list[Check]:
    """Check that the values the labels will carry describe the files actually present."""
    checks = [
        Check(
            "provenance.dataset_sha256",
            identity["dataset"]["sha256"] == record["sha256"],
            "dataset matches its provenance record"
            if identity["dataset"]["sha256"] == record["sha256"]
            else f"record says {record['sha256']}, file is {identity['dataset']['sha256']}",
        ),
        Check(
            "provenance.model_identity",
            identity["model"]["name"] == model_contract.name
            and identity["model"]["version"] == model_contract.version,
            f"model.json is {identity['model']['name']} {identity['model']['version']} "
            f"(contract {model_contract.name} {model_contract.version})",
        ),
    ]
    for key, expected in sorted(expectations.items()):
        actual = _lookup(identity, key)
        if expected == "":
            checks.append(Check(f"build_arg.{key}", False, "build arg is empty"))
        elif actual is None:
            checks.append(Check(f"build_arg.{key}", False, f"unknown key {key}"))
        else:
            ok = str(actual) == expected
            checks.append(
                Check(
                    f"build_arg.{key}",
                    ok,
                    "matches the file"
                    if ok
                    else f"build arg says {expected!r}, file is {actual!r}",
                )
            )
    return checks


def write_manifest(
    identity: dict[str, Any],
    root: Path,
    files: list[str],
    gates: dict[str, str],
    out: Path,
) -> dict[str, Any]:
    """Record every shipped file by hash, relative to the image's app root."""
    missing = [f for f in files if not (root / f).is_file()]
    if missing:
        raise ConfigError(f"cannot write manifest, missing files: {missing}")
    manifest = {
        "manifest_version": MANIFEST_VERSION,
        **identity,
        "gates": gates,
        "files": {f: sha256_file(root / f) for f in sorted(files)},
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def selfcheck(manifest_path: Path) -> tuple[list[Check], dict[str, Any]]:
    """Re-hash every file the manifest lists. The app root is the manifest's grandparent."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    root = manifest_path.resolve().parent.parent
    checks: list[Check] = []
    for rel, expected in sorted(manifest.get("files", {}).items()):
        path = root / rel
        if not path.is_file():
            checks.append(Check(f"file.{rel}", False, "missing from the image"))
            continue
        actual = sha256_file(path)
        checks.append(
            Check(
                f"file.{rel}",
                actual == expected,
                "hash matches" if actual == expected else "CHANGED",
            )
        )
    failed_gates = [g for g, status in manifest.get("gates", {}).items() if status != "passed"]
    checks.append(
        Check(
            "gates.recorded",
            not failed_gates and bool(manifest.get("gates")),
            "all build gates recorded as passed" if not failed_gates else f"failed: {failed_gates}",
        )
    )
    return checks, {"status": gate_status(checks), "manifest": manifest}
