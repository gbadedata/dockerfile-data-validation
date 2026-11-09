from __future__ import annotations

import json

import pytest
from conftest import DATA_CONTRACT, DATASET, DOCKERFILE, MODEL_CONTRACT, MODEL_DIR, RECORD, STANDARD

from dvgate.cli import _read_labels
from dvgate.common import ConfigError
from dvgate.contracts import load_data_contract, load_dataset_record, load_model_contract
from dvgate.labels import check_labels, lint_dockerfile, load_standard
from dvgate.manifest import describe_inputs


@pytest.fixture(scope="module")
def standard():
    return load_standard(STANDARD)


@pytest.fixture(scope="module")
def manifest(standard):
    return describe_inputs(
        load_dataset_record(RECORD),
        DATASET,
        load_data_contract(DATA_CONTRACT),
        load_model_contract(MODEL_CONTRACT),
        MODEL_DIR,
        standard,
    )


@pytest.fixture
def labels(manifest):
    """A complete, correct label set, as scripts/build-image.sh would produce."""
    return {
        "org.opencontainers.image.title": "dockerfile-data-validation",
        "org.opencontainers.image.description": "validated data image",
        "org.opencontainers.image.version": "1.0.0",
        "org.opencontainers.image.revision": "a" * 40,
        "org.opencontainers.image.created": "2026-11-09T09:00:00Z",
        "org.opencontainers.image.source": "https://github.com/gbadedata/dockerfile-data-validation",
        "org.opencontainers.image.licenses": "MIT AND CC0-1.0",
        **{f"io.gbadedata.dataset.{k}": v for k, v in manifest["dataset"].items()},
        **{f"io.gbadedata.schema.{k}": v for k, v in manifest["schema"].items()},
        **{f"io.gbadedata.model.{k}": v for k, v in manifest["model"].items()},
        "io.gbadedata.validation.report": "/app/reports/manifest.json",
        "io.gbadedata.validation.policy": "fail-closed",
        "io.gbadedata.label-standard.version": "1.0.0",
    }


def failures(checks) -> set[str]:
    return {c.id for c in checks if not c.passed}


def test_complete_labels_pass_with_manifest(labels, standard, manifest):
    checks, report = check_labels(labels, standard, manifest)
    assert not failures(checks)
    assert report["status"] == "passed"


@pytest.mark.parametrize(
    "key, value",
    [
        ("io.gbadedata.dataset.sha256", "F204DB2C"),  # not 64 lowercase hex
        ("org.opencontainers.image.created", "09/11/2026"),  # not RFC 3339
        ("org.opencontainers.image.revision", "main"),  # a branch, not a commit
        ("org.opencontainers.image.version", "v1"),  # not semver
        ("io.gbadedata.validation.policy", "best-effort"),  # not an allowed value
        ("io.gbadedata.label-standard.version", "0.9.0"),  # wrong standard version
    ],
)
def test_malformed_label_fails(labels, standard, key, value):
    labels[key] = value
    assert f"label.{key}" in failures(check_labels(labels, standard)[0])


def test_missing_and_empty_labels_fail(labels, standard):
    del labels["io.gbadedata.model.sha256"]
    labels["io.gbadedata.schema.version"] = ""
    found = failures(check_labels(labels, standard)[0])
    assert {"label.io.gbadedata.model.sha256", "label.io.gbadedata.schema.version"} <= found


def test_label_that_disagrees_with_manifest_fails(labels, standard, manifest):
    labels["io.gbadedata.dataset.sha256"] = "b" * 64  # well-formed, but untrue
    assert failures(check_labels(labels, standard)[0]) == set()  # format alone passes
    found = failures(check_labels(labels, standard, manifest)[0])
    assert found == {"label.io.gbadedata.dataset.sha256"}


def test_typo_in_namespace_fails(labels, standard):
    labels["io.gbadedata.dataset.sha265"] = "c" * 64
    assert "standard.unknown_keys" in failures(check_labels(labels, standard)[0])


def test_secret_in_label_fails(labels, standard):
    labels["build.note"] = "api_key=abc123"
    assert "standard.no_secrets" in failures(check_labels(labels, standard)[0])


def test_repository_dockerfile_declares_every_label(standard):
    checks, _ = lint_dockerfile(DOCKERFILE, standard)
    assert not failures(checks)


def test_lint_catches_missing_label(standard, tmp_path):
    text = DOCKERFILE.read_text()
    broken = tmp_path / "Dockerfile"
    broken.write_text("\n".join(ln for ln in text.splitlines() if "dataset.licence=" not in ln))
    assert "declared.io.gbadedata.dataset.licence" in failures(lint_dockerfile(broken, standard)[0])


def test_lint_catches_label_from_undeclared_arg(standard, tmp_path):
    broken = tmp_path / "Dockerfile"
    text = DOCKERFILE.read_text()
    cut = text.rindex("ARG MODEL_SHA256\n")  # the runtime stage's declaration
    broken.write_text(text[:cut] + text[cut + len("ARG MODEL_SHA256\n") :])
    found = failures(lint_dockerfile(broken, standard)[0])
    assert "declared.io.gbadedata.model.sha256" in found


def test_lint_rejects_legacy_label_syntax(standard, tmp_path):
    legacy = tmp_path / "Dockerfile"
    legacy.write_text("FROM scratch\nLABEL maintainer someone\n")
    with pytest.raises(ConfigError, match="legacy"):
        lint_dockerfile(legacy, standard)


def test_labels_read_from_docker_inspect_output(tmp_path, labels):
    raw = tmp_path / "inspect.json"
    raw.write_text(json.dumps([{"Config": {"Labels": labels}}]))
    assert _read_labels(str(raw)) == labels
    empty = tmp_path / "none.json"
    empty.write_text("null")
    assert _read_labels(str(empty)) == {}
