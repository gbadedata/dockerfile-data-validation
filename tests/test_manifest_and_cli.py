from __future__ import annotations

import json
import shutil

import pytest
from conftest import DATA_CONTRACT, DATASET, MODEL_CONTRACT, MODEL_DIR, RECORD, ROOT, STANDARD

from dvgate import EXIT_CONFIG_ERROR, EXIT_GATE_FAILED, EXIT_OK
from dvgate.cli import main
from dvgate.contracts import load_data_contract, load_dataset_record, load_model_contract
from dvgate.labels import load_standard
from dvgate.manifest import describe_inputs, selfcheck, verify_inputs, write_manifest


@pytest.fixture
def identity():
    return describe_inputs(
        load_dataset_record(RECORD),
        DATASET,
        load_data_contract(DATA_CONTRACT),
        load_model_contract(MODEL_CONTRACT),
        MODEL_DIR,
        load_standard(STANDARD),
    )


def test_build_args_that_match_the_files_pass(identity):
    expect = {"dataset.sha256": identity["dataset"]["sha256"], "model.version": "1.0.0"}
    checks = verify_inputs(
        identity, load_dataset_record(RECORD), load_model_contract(MODEL_CONTRACT), expect
    )
    assert all(c.passed for c in checks)


def test_lying_or_empty_build_args_fail(identity):
    expect = {"dataset.sha256": "0" * 64, "schema.version": ""}
    checks = verify_inputs(
        identity, load_dataset_record(RECORD), load_model_contract(MODEL_CONTRACT), expect
    )
    assert {c.id for c in checks if not c.passed} == {
        "build_arg.dataset.sha256",
        "build_arg.schema.version",
    }


def test_dataset_not_matching_its_record_fails(identity):
    record = dict(load_dataset_record(RECORD), sha256="1" * 64)
    checks = verify_inputs(identity, record, load_model_contract(MODEL_CONTRACT), {})
    assert "provenance.dataset_sha256" in {c.id for c in checks if not c.passed}


@pytest.fixture
def app_root(tmp_path, identity):
    """A miniature copy of the image's /app layout with a manifest."""
    for rel in ("data/dataset.csv", "model/model.json"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(DATASET, tmp_path / "data/dataset.csv")
    shutil.copy(MODEL_DIR / "model.json", tmp_path / "model/model.json")
    write_manifest(
        identity,
        tmp_path,
        ["data/dataset.csv", "model/model.json"],
        {"data": "passed", "model": "passed"},
        tmp_path / "reports/manifest.json",
    )
    return tmp_path


def test_selfcheck_passes_on_untouched_files(app_root):
    checks, _ = selfcheck(app_root / "reports/manifest.json")
    assert all(c.passed for c in checks)


def test_selfcheck_detects_a_changed_file(app_root):
    with open(app_root / "data/dataset.csv", "a") as handle:
        handle.write("Adelie,Dream,40,18,190,3800,male,2008\n")
    checks, _ = selfcheck(app_root / "reports/manifest.json")
    assert [c.id for c in checks if not c.passed] == ["file.data/dataset.csv"]


def test_selfcheck_rejects_a_failed_gate(app_root):
    path = app_root / "reports/manifest.json"
    manifest = json.loads(path.read_text())
    manifest["gates"]["model"] = "failed"
    path.write_text(json.dumps(manifest))
    assert "gates.recorded" in {c.id for c in selfcheck(path)[0] if not c.passed}


def test_cli_exit_codes(tmp_path, monkeypatch):
    monkeypatch.chdir(ROOT)
    good = ["validate-data", "--dataset", str(DATASET), "--contract", str(DATA_CONTRACT)]
    assert main(good) == EXIT_OK
    bad = [
        "validate-data",
        "--dataset",
        "fixtures/missing-column/penguins.csv",
        "--contract",
        str(DATA_CONTRACT),
    ]
    assert main(bad) == EXIT_GATE_FAILED
    missing = ["validate-data", "--dataset", str(DATASET), "--contract", "nope.yaml"]
    assert main(missing) == EXIT_CONFIG_ERROR


def test_cli_build_args_are_derived_from_files(capsys, monkeypatch):
    monkeypatch.chdir(ROOT)
    assert main(["build-args"]) == EXIT_OK
    pairs = dict(line.split("=", 1) for line in capsys.readouterr().out.splitlines())
    assert pairs["DATASET_SHA256"] == load_dataset_record(RECORD)["sha256"]
    assert pairs["MODEL_SHA256"] and pairs["SCHEMA_SHA256"] and pairs["IMAGE_VERSION"] == "1.0.0"


def test_fixture_records_match_their_files():
    """The broken fixtures must get past provenance checks, so they fail at the intended gate."""
    from dvgate.common import sha256_file

    for name in ("bad-values", "missing-column"):
        record = load_dataset_record(ROOT / "fixtures" / name / "dataset.yaml")
        assert sha256_file(ROOT / record["file"]) == record["sha256"], name
