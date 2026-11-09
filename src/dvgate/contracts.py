"""Loading and checking the data contract, model contract and dataset record.

Each loader validates the file's own structure first, so a typo in a contract
is reported as a configuration error instead of quietly weakening a gate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .common import ConfigError, load_yaml, require_keys, sha256_file

SEMVER = re.compile(r"^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$")
COLUMN_TYPES = {"string", "float", "integer"}
MISSING_POLICIES = {"quarantine", "warn", "allow"}


@dataclass(frozen=True)
class ColumnSpec:
    name: str
    type: str
    allowed: tuple[str, ...] | None = None
    range: tuple[float, float] | None = None
    missing: str = "quarantine"


@dataclass(frozen=True)
class DataContract:
    name: str
    version: str
    path: Path
    sha256: str
    delimiter: str
    encoding: str
    na_values: tuple[str, ...]
    extra_columns: str
    columns: tuple[ColumnSpec, ...]
    dataset_rules: dict[str, Any] = field(default_factory=dict)

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    def column(self, name: str) -> ColumnSpec:
        for col in self.columns:
            if col.name == name:
                return col
        raise ConfigError(f"column '{name}' is not in data contract {self.name}")


def load_data_contract(path: str | Path) -> DataContract:
    path = Path(path)
    raw = load_yaml(path)
    where = f"data contract {path}"
    require_keys(raw, ["name", "version", "format", "columns"], where)
    if not SEMVER.match(str(raw["version"])):
        raise ConfigError(f"{where}: version '{raw['version']}' is not semver")

    fmt = raw["format"] or {}
    if fmt.get("type") != "csv":
        raise ConfigError(f"{where}: only format.type 'csv' is supported")

    extra = raw.get("extra_columns", "reject")
    if extra not in {"reject", "allow"}:
        raise ConfigError(f"{where}: extra_columns must be 'reject' or 'allow'")

    columns: list[ColumnSpec] = []
    seen: set[str] = set()
    for i, col in enumerate(raw["columns"]):
        require_keys(col, ["name", "type"], f"{where} columns[{i}]")
        name = str(col["name"])
        if name in seen:
            raise ConfigError(f"{where}: column '{name}' is declared twice")
        seen.add(name)
        if col["type"] not in COLUMN_TYPES:
            raise ConfigError(f"{where}: column '{name}' has unknown type '{col['type']}'")
        missing = col.get("missing", "quarantine")
        if missing not in MISSING_POLICIES:
            raise ConfigError(f"{where}: column '{name}' has unknown missing policy '{missing}'")
        allowed = col.get("allowed")
        bounds = col.get("range")
        if allowed is not None and col["type"] != "string":
            raise ConfigError(f"{where}: 'allowed' only applies to string column '{name}'")
        if bounds is not None:
            if col["type"] == "string" or len(bounds) != 2 or bounds[0] > bounds[1]:
                raise ConfigError(f"{where}: column '{name}' has an invalid range {bounds}")
        columns.append(
            ColumnSpec(
                name=name,
                type=col["type"],
                allowed=tuple(str(v) for v in allowed) if allowed is not None else None,
                range=(float(bounds[0]), float(bounds[1])) if bounds is not None else None,
                missing=missing,
            )
        )

    rules = raw.get("dataset_rules") or {}
    known_rules = {
        "row_count",
        "max_duplicate_rows",
        "max_quarantine_fraction",
        "require_all_categories",
        "min_category_share",
    }
    unknown = set(rules) - known_rules
    if unknown:
        raise ConfigError(f"{where}: unknown dataset rules: {', '.join(sorted(unknown))}")
    for col_name in rules.get("require_all_categories", []):
        spec = next((c for c in columns if c.name == col_name), None)
        if spec is None or spec.allowed is None:
            raise ConfigError(
                f"{where}: require_all_categories needs an 'allowed' list on '{col_name}'"
            )

    return DataContract(
        name=str(raw["name"]),
        version=str(raw["version"]),
        path=path,
        sha256=sha256_file(path),
        delimiter=fmt.get("delimiter", ","),
        encoding=fmt.get("encoding", "utf-8"),
        na_values=tuple(str(v) for v in fmt.get("na_values", ["NA", ""])),
        extra_columns=extra,
        columns=tuple(columns),
        dataset_rules=rules,
    )


@dataclass(frozen=True)
class ModelContract:
    name: str
    version: str
    path: Path
    format: str
    files: tuple[str, ...]
    target: str
    features: tuple[str, ...]
    test_fraction: float
    seed: int
    thresholds: dict[str, float]
    card_tolerance: float
    require_dataset_sha256_match: bool
    require_data_contract_version_match: bool


def load_model_contract(path: str | Path) -> ModelContract:
    path = Path(path)
    raw = load_yaml(path)
    where = f"model contract {path}"
    require_keys(raw, ["name", "version", "artefact", "target", "features", "evaluation"], where)
    if not SEMVER.match(str(raw["version"])):
        raise ConfigError(f"{where}: version '{raw['version']}' is not semver")
    art = raw["artefact"]
    require_keys(art, ["format", "files"], f"{where} artefact")
    ev = raw["evaluation"]
    require_keys(ev, ["test_fraction", "seed", "thresholds"], f"{where} evaluation")
    if not 0 < float(ev["test_fraction"]) < 1:
        raise ConfigError(f"{where}: evaluation.test_fraction must be between 0 and 1")
    unknown_metrics = set(ev["thresholds"]) - {"accuracy", "macro_f1"}
    if unknown_metrics:
        raise ConfigError(
            f"{where}: unknown threshold metrics: {', '.join(sorted(unknown_metrics))}"
        )
    lineage = raw.get("lineage") or {}
    return ModelContract(
        name=str(raw["name"]),
        version=str(raw["version"]),
        path=path,
        format=str(art["format"]),
        files=tuple(art["files"]),
        target=str(raw["target"]),
        features=tuple(str(f) for f in raw["features"]),
        test_fraction=float(ev["test_fraction"]),
        seed=int(ev["seed"]),
        thresholds={k: float(v) for k, v in ev["thresholds"].items()},
        card_tolerance=float(ev.get("card_tolerance", 1e-6)),
        require_dataset_sha256_match=bool(lineage.get("require_dataset_sha256_match", True)),
        require_data_contract_version_match=bool(
            lineage.get("require_data_contract_version_match", True)
        ),
    )


DATASET_RECORD_KEYS = ["name", "version", "file", "sha256", "source", "revision", "licence"]


def load_dataset_record(path: str | Path) -> dict[str, Any]:
    raw = load_yaml(path)
    require_keys(raw, DATASET_RECORD_KEYS, f"dataset record {path}")
    return {
        k: str(raw[k]) for k in raw if k in DATASET_RECORD_KEYS + ["upstream_path", "description"]
    }
