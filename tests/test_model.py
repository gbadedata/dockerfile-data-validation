from __future__ import annotations

import json
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest
from conftest import DATA_CONTRACT, DATASET, MODEL_CONTRACT, MODEL_DIR

from dvgate.common import sha256_file
from dvgate.contracts import load_data_contract, load_model_contract
from dvgate.data import validate_dataset
from dvgate.model import stratified_split, train, validate_model

DATA_SHA = sha256_file(DATASET)


@pytest.fixture(scope="module")
def contracts():
    return load_data_contract(DATA_CONTRACT), load_model_contract(MODEL_CONTRACT)


@pytest.fixture(scope="module")
def accepted(contracts):
    return validate_dataset(DATASET, contracts[0]).accepted


def run(model_dir, contracts, accepted, dataset_sha=DATA_SHA):
    data_contract, model_contract = contracts
    checks, report = validate_model(model_dir, model_contract, data_contract, accepted, dataset_sha)
    return {c.id for c in checks if not c.passed}, report


def edit(path, change):
    payload = json.loads(path.read_text())
    change(payload)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def test_committed_model_passes(contracts, accepted):
    failures, report = run(MODEL_DIR, contracts, accepted)
    assert not failures
    assert report["measured"]["accuracy"] >= 0.95


def test_training_is_byte_for_byte_reproducible(contracts, accepted, tmp_path):
    pytest.importorskip("sklearn")
    data_contract, model_contract = contracts
    train(accepted, model_contract, data_contract, DATA_SHA, tmp_path)
    for name in ("model.json", "model_card.json"):
        assert (tmp_path / name).read_bytes() == (MODEL_DIR / name).read_bytes(), name


def test_inflated_card_is_caught(contracts, accepted, model_copy):
    edit(
        model_copy / "model_card.json", lambda c: c["evaluation"]["metrics"].update(accuracy=0.999)
    )
    failures, _ = run(model_copy, contracts, accepted)
    assert failures == {"card.accuracy"}


def test_model_trained_on_other_data_fails_lineage(contracts, accepted):
    failures, _ = run(MODEL_DIR, contracts, accepted, dataset_sha="0" * 64)
    assert "lineage.dataset_sha256" in failures


def test_feature_drift_fails(contracts, accepted, model_copy):
    edit(model_copy / "model.json", lambda m: m["features"].reverse())
    assert "schema.features" in run(model_copy, contracts, accepted)[0]


def test_non_finite_parameters_fail(contracts, accepted, model_copy):
    edit(model_copy / "model.json", lambda m: m["coef"][0].__setitem__(0, float("nan")))
    assert "artefact.parameters" in run(model_copy, contracts, accepted)[0]


def test_missing_card_fails(contracts, accepted, model_copy):
    (model_copy / "model_card.json").unlink()
    assert "artefact.files" in run(model_copy, contracts, accepted)[0]


def test_threshold_above_model_quality_fails(contracts, accepted):
    data_contract, model_contract = contracts
    strict = replace(model_contract, thresholds={"accuracy": 0.999, "macro_f1": 0.95})
    checks, _ = validate_model(MODEL_DIR, strict, data_contract, accepted, DATA_SHA)
    assert {c.id for c in checks if not c.passed} == {"evaluation.accuracy"}


def test_stratified_split_is_deterministic_and_proportional():
    labels = pd.Series(["a"] * 50 + ["b"] * 30 + ["c"] * 20)
    first = stratified_split(labels, 0.2, seed=7)
    assert np.array_equal(first, stratified_split(labels, 0.2, seed=7))
    assert not np.array_equal(first, stratified_split(labels, 0.2, seed=8))
    assert labels[first].value_counts().to_dict() == {"a": 10, "b": 6, "c": 4}
