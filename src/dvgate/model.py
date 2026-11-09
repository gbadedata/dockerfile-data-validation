"""Model artefact gate, plus the training step that produces the artefact.

The artefact is a multinomial logistic regression stored as plain JSON
(feature scaling, coefficients, intercepts), so it can be hashed, diffed and
loaded without unpickling anything. Training needs scikit-learn; validation
needs only NumPy.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .common import Check, ConfigError, gate_status, sha256_file
from .contracts import DataContract, ModelContract

DECIMALS = 8  # rounding keeps the JSON byte-stable across platforms


def stratified_split(labels: pd.Series, test_fraction: float, seed: int) -> np.ndarray:
    """Boolean mask of test rows: the same fraction of each class, chosen by a seeded shuffle."""
    rng = np.random.default_rng(seed)
    is_test = np.zeros(len(labels), dtype=bool)
    values = labels.to_numpy()
    for cls in sorted(pd.unique(values)):
        idx = np.flatnonzero(values == cls)
        idx = idx[rng.permutation(len(idx))]
        n_test = int(round(len(idx) * test_fraction))
        is_test[idx[:n_test]] = True
    return is_test


def model_frame(accepted: pd.DataFrame, contract: ModelContract) -> tuple[np.ndarray, pd.Series]:
    missing = [f for f in (*contract.features, contract.target) if f not in accepted.columns]
    if missing:
        raise ConfigError(f"accepted data has no column(s) {missing}")
    frame = accepted.dropna(subset=[*contract.features, contract.target]).reset_index(drop=True)
    return frame[list(contract.features)].to_numpy(dtype="float64"), frame[contract.target]


def predict(model: dict[str, Any], features: np.ndarray) -> np.ndarray:
    mean = np.asarray(model["scaler"]["mean"], dtype="float64")
    scale = np.asarray(model["scaler"]["scale"], dtype="float64")
    coef = np.asarray(model["coef"], dtype="float64")
    intercept = np.asarray(model["intercept"], dtype="float64")
    scores = ((features - mean) / scale) @ coef.T + intercept
    return np.asarray(model["classes"])[scores.argmax(axis=1)]


def score(y_true: np.ndarray, y_pred: np.ndarray, classes: list[str]) -> dict[str, float]:
    accuracy = float(np.mean(y_true == y_pred))
    f1s = []
    for cls in classes:
        tp = float(np.sum((y_true == cls) & (y_pred == cls)))
        fp = float(np.sum((y_true != cls) & (y_pred == cls)))
        fn = float(np.sum((y_true == cls) & (y_pred != cls)))
        denom = 2 * tp + fp + fn
        f1s.append(2 * tp / denom if denom else 0.0)
    return {"accuracy": round(accuracy, DECIMALS), "macro_f1": round(float(np.mean(f1s)), DECIMALS)}


def train(
    accepted: pd.DataFrame,
    contract: ModelContract,
    data_contract: DataContract,
    dataset_sha256: str,
    out_dir: str | Path,
) -> dict[str, Any]:
    try:
        from sklearn.linear_model import LogisticRegression
    except ImportError as exc:  # pragma: no cover - depends on install extras
        raise ConfigError("training needs scikit-learn: pip install 'dvgate[train]'") from exc

    features, labels = model_frame(accepted, contract)
    is_test = stratified_split(labels, contract.test_fraction, contract.seed)
    x_train, y_train = features[~is_test], labels[~is_test].to_numpy()
    x_test, y_test = features[is_test], labels[is_test].to_numpy()

    mean = x_train.mean(axis=0)
    scale = x_train.std(axis=0)
    scale[scale == 0] = 1.0
    clf = LogisticRegression(max_iter=1000, C=1.0)
    clf.fit((x_train - mean) / scale, y_train)

    def r(values: np.ndarray) -> list:
        return np.round(values, DECIMALS).tolist()

    model = {
        "format": contract.format,
        "name": contract.name,
        "version": contract.version,
        "target": contract.target,
        "features": list(contract.features),
        "classes": [str(c) for c in clf.classes_],
        "scaler": {"mean": r(mean), "scale": r(scale)},
        "coef": r(clf.coef_),
        "intercept": r(clf.intercept_),
        "training": {
            "dataset_sha256": dataset_sha256,
            "data_contract": data_contract.name,
            "data_contract_version": data_contract.version,
            "seed": contract.seed,
            "test_fraction": contract.test_fraction,
        },
    }
    metrics = score(y_test, predict(model, x_test), model["classes"])
    card = {
        "model": contract.name,
        "version": contract.version,
        "intended_use": "Teaching example: classify penguin species from four body measurements.",
        "evaluation": {
            "split": "stratified holdout",
            "seed": contract.seed,
            "test_fraction": contract.test_fraction,
            "n_train": int((~is_test).sum()),
            "n_test": int(is_test.sum()),
            "metrics": metrics,
        },
        "limitations": "Trained on 2007-2009 data from three islands near Palmer Station; "
        "not validated for other populations or years.",
    }
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, payload in (("model.json", model), ("model_card.json", card)):
        (out_dir / name).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return card


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path.name} is not valid JSON: {exc}") from exc


def validate_model(
    model_dir: str | Path,
    contract: ModelContract,
    data_contract: DataContract,
    accepted: pd.DataFrame,
    dataset_sha256: str,
) -> tuple[list[Check], dict[str, Any]]:
    model_dir = Path(model_dir)
    checks: list[Check] = []

    missing = [f for f in contract.files if not (model_dir / f).is_file()]
    checks.append(
        Check(
            "artefact.files",
            not missing,
            f"present: {list(contract.files)}" if not missing else f"missing: {missing}",
        )
    )
    if missing:
        return checks, _report(checks, contract, model_dir, {})

    try:
        model = _load_json(model_dir / "model.json")
        card = _load_json(model_dir / "model_card.json")
    except ValueError as exc:
        checks.append(Check("artefact.json", False, str(exc)))
        return checks, _report(checks, contract, model_dir, {})

    checks.append(
        Check(
            "artefact.format",
            model.get("format") == contract.format,
            f"format {model.get('format')!r} (expected {contract.format!r})",
        )
    )
    checks.append(
        Check(
            "artefact.identity",
            model.get("name") == contract.name and model.get("version") == contract.version,
            f"{model.get('name')} {model.get('version')} "
            f"(contract {contract.name} {contract.version})",
        )
    )

    feature_ok = model.get("features") == list(contract.features)
    checks.append(
        Check(
            "schema.features",
            feature_ok,
            "features match the contract"
            if feature_ok
            else f"model features {model.get('features')} != contract {list(contract.features)}",
        )
    )
    non_numeric = [
        f
        for f in contract.features
        if f not in data_contract.column_names or data_contract.column(f).type == "string"
    ]
    checks.append(
        Check(
            "schema.features_in_data_contract",
            not non_numeric,
            "every feature is a numeric column in the data contract"
            if not non_numeric
            else f"not numeric columns in the data contract: {non_numeric}",
        )
    )
    expected_classes = sorted(data_contract.column(contract.target).allowed or ())
    checks.append(
        Check(
            "schema.classes",
            sorted(model.get("classes", [])) == expected_classes,
            f"classes {model.get('classes')} (data contract allows {expected_classes})",
        )
    )

    shape_ok, shape_detail = _check_shapes(model)
    checks.append(Check("artefact.parameters", shape_ok, shape_detail))

    training = model.get("training", {})
    if contract.require_dataset_sha256_match:
        checks.append(
            Check(
                "lineage.dataset_sha256",
                training.get("dataset_sha256") == dataset_sha256,
                "trained on this exact dataset"
                if training.get("dataset_sha256") == dataset_sha256
                else f"trained on {training.get('dataset_sha256')}, image ships {dataset_sha256}",
            )
        )
    if contract.require_data_contract_version_match:
        checks.append(
            Check(
                "lineage.data_contract_version",
                training.get("data_contract_version") == data_contract.version,
                f"trained under data contract {training.get('data_contract_version')} "
                f"(current {data_contract.version})",
            )
        )

    measured: dict[str, float] = {}
    if all(c.passed for c in checks):
        measured = _evaluate(model, card, contract, accepted, checks)
    return checks, _report(checks, contract, model_dir, measured)


def _check_shapes(model: dict[str, Any]) -> tuple[bool, str]:
    try:
        n_features = len(model["features"])
        n_classes = len(model["classes"])
        coef = np.asarray(model["coef"], dtype="float64")
        intercept = np.asarray(model["intercept"], dtype="float64")
        mean = np.asarray(model["scaler"]["mean"], dtype="float64")
        scale = np.asarray(model["scaler"]["scale"], dtype="float64")
    except (KeyError, TypeError, ValueError) as exc:
        return False, f"parameters unreadable: {exc}"
    if coef.shape != (n_classes, n_features) or intercept.shape != (n_classes,):
        return (
            False,
            f"coef {coef.shape} / intercept {intercept.shape} do not fit {n_classes} classes",
        )
    if mean.shape != (n_features,) or scale.shape != (n_features,):
        return False, "scaler does not match the feature count"
    arrays = (coef, intercept, mean, scale)
    if not all(np.isfinite(a).all() for a in arrays) or (scale <= 0).any():
        return False, "parameters contain NaN, infinity or a non-positive scale"
    return True, f"{n_classes} classes x {n_features} features, all finite"


def _evaluate(
    model: dict[str, Any],
    card: dict[str, Any],
    contract: ModelContract,
    accepted: pd.DataFrame,
    checks: list[Check],
) -> dict[str, float]:
    ev = card.get("evaluation", {})
    same_split = ev.get("seed") == contract.seed and math.isclose(
        float(ev.get("test_fraction", -1)), contract.test_fraction
    )
    checks.append(
        Check(
            "evaluation.split",
            same_split,
            f"card split seed={ev.get('seed')} fraction={ev.get('test_fraction')} "
            f"(contract seed={contract.seed} fraction={contract.test_fraction})",
        )
    )
    features, labels = model_frame(accepted, contract)
    is_test = stratified_split(labels, contract.test_fraction, contract.seed)
    measured = score(
        labels[is_test].to_numpy(), predict(model, features[is_test]), model["classes"]
    )
    claimed = ev.get("metrics", {})

    for metric, floor in contract.thresholds.items():
        value = measured[metric]
        checks.append(
            Check(
                f"evaluation.{metric}",
                value >= floor,
                f"re-scored {value:.4f} on {int(is_test.sum())} holdout rows (floor {floor})",
            )
        )
        card_value = claimed.get(metric)
        honest = (
            card_value is not None and abs(float(card_value) - value) <= contract.card_tolerance
        )
        checks.append(
            Check(
                f"card.{metric}",
                honest,
                f"card says {card_value}, re-scored {value}"
                + ("" if honest else f" (tolerance {contract.card_tolerance})"),
            )
        )
    return measured


def _report(
    checks: list[Check], contract: ModelContract, model_dir: Path, measured: dict[str, float]
) -> dict[str, Any]:
    files = {f: sha256_file(model_dir / f) for f in contract.files if (model_dir / f).is_file()}
    return {
        "gate": "model",
        "status": gate_status(checks),
        "contract": {"name": contract.name, "version": contract.version},
        "files": files,
        "measured": measured,
        "checks": [c.as_dict() for c in checks],
    }
