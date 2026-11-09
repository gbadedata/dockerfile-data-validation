"""Small shared pieces: errors, hashing, YAML loading and check records."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml


class ConfigError(Exception):
    """A contract, standard or input is malformed. Distinct from a failed gate."""


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_yaml(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"file not found: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a mapping at the top level")
    return data


def require_keys(mapping: dict[str, Any], keys: list[str], where: str) -> None:
    missing = [k for k in keys if k not in mapping or mapping[k] in (None, "")]
    if missing:
        raise ConfigError(f"{where} is missing required keys: {', '.join(missing)}")


@dataclass
class Check:
    """One named check and its outcome, in the form every report uses."""

    id: str
    passed: bool
    detail: str
    severity: str = "error"  # error fails the gate; warn is recorded only
    data: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        if not out["data"]:
            out.pop("data")
        return out


def gate_status(checks: list[Check]) -> str:
    failed = [c for c in checks if not c.passed and c.severity == "error"]
    return "failed" if failed else "passed"


def write_json(path: str | Path, payload: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def print_checks(title: str, checks: list[Check]) -> None:
    status = gate_status(checks)
    print(f"{title}: {status.upper()}")
    for check in checks:
        if check.passed:
            mark = "ok  "
        elif check.severity == "warn":
            mark = "WARN"
        else:
            mark = "FAIL"
        print(f"  [{mark}] {check.id}: {check.detail}")
