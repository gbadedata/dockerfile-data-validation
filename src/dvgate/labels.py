"""Label standard: static Dockerfile lint and post-build label verification."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .common import Check, ConfigError, gate_status, load_yaml


@dataclass(frozen=True)
class LabelSpec:
    key: str
    pattern: re.Pattern[str] | None
    pattern_name: str | None
    allowed: tuple[str, ...] | None
    manifest: str | None
    equals_standard_version: bool


@dataclass(frozen=True)
class LabelStandard:
    name: str
    version: str
    namespace: str
    labels: tuple[LabelSpec, ...]
    unknown_namespace_keys: str
    secret_pattern: re.Pattern[str] | None

    @property
    def keys(self) -> list[str]:
        return [spec.key for spec in self.labels]


def load_standard(path: str | Path) -> LabelStandard:
    raw = load_yaml(path)
    where = f"label standard {path}"
    for key in ("standard", "version", "namespace", "labels"):
        if key not in raw:
            raise ConfigError(f"{where} is missing '{key}'")
    patterns: dict[str, re.Pattern[str]] = {}
    for name, regex in (raw.get("patterns") or {}).items():
        try:
            patterns[name] = re.compile(regex)
        except re.error as exc:
            raise ConfigError(f"{where}: pattern '{name}' does not compile: {exc}") from exc

    specs: list[LabelSpec] = []
    for key, spec in raw["labels"].items():
        spec = spec or {}
        pattern_name = spec.get("pattern")
        if pattern_name is not None and pattern_name not in patterns:
            raise ConfigError(f"{where}: label {key} uses unknown pattern '{pattern_name}'")
        if pattern_name is None and "allowed" not in spec:
            raise ConfigError(f"{where}: label {key} needs a pattern or an allowed list")
        specs.append(
            LabelSpec(
                key=key,
                pattern=patterns.get(pattern_name) if pattern_name else None,
                pattern_name=pattern_name,
                allowed=tuple(spec["allowed"]) if "allowed" in spec else None,
                manifest=spec.get("manifest"),
                equals_standard_version=bool(spec.get("equals_standard_version", False)),
            )
        )
    rules = raw.get("rules") or {}
    secret = rules.get("secret_pattern")
    return LabelStandard(
        name=str(raw["standard"]),
        version=str(raw["version"]),
        namespace=str(raw["namespace"]),
        labels=tuple(specs),
        unknown_namespace_keys=str(rules.get("unknown_namespace_keys", "error")),
        secret_pattern=re.compile(secret) if secret else None,
    )


def _lookup(document: dict[str, Any], dotted: str) -> Any:
    node: Any = document
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _common_checks(labels: dict[str, str], standard: LabelStandard) -> list[Check]:
    checks: list[Check] = []
    prefix = standard.namespace + "."
    unknown = sorted(k for k in labels if k.startswith(prefix) and k not in standard.keys)
    if standard.unknown_namespace_keys == "error":
        checks.append(
            Check(
                "standard.unknown_keys",
                not unknown,
                "no unknown keys in the namespace"
                if not unknown
                else f"not in the standard: {unknown}",
            )
        )
    if standard.secret_pattern is not None:
        leaks = sorted(
            k
            for k, v in labels.items()
            if standard.secret_pattern.search(k) or standard.secret_pattern.search(str(v))
        )
        checks.append(
            Check(
                "standard.no_secrets",
                not leaks,
                "no credential-like keys or values" if not leaks else f"credential-like: {leaks}",
            )
        )
    return checks


def check_labels(
    labels: dict[str, str],
    standard: LabelStandard,
    manifest: dict[str, Any] | None = None,
) -> tuple[list[Check], dict[str, Any]]:
    """Verify a built image's labels against the standard and, optionally, its manifest."""
    labels = labels or {}
    checks: list[Check] = []
    for spec in standard.labels:
        value = labels.get(spec.key)
        if value is None or str(value).strip() == "":
            checks.append(Check(f"label.{spec.key}", False, "missing or empty"))
            continue
        problems = []
        if spec.pattern is not None and not spec.pattern.match(value):
            problems.append(f"{value!r} does not match {spec.pattern_name}")
        if spec.allowed is not None and value not in spec.allowed:
            problems.append(f"{value!r} not in {list(spec.allowed)}")
        if spec.equals_standard_version and value != standard.version:
            problems.append(f"{value!r} but the standard is {standard.version}")
        if spec.manifest and manifest is not None:
            expected = _lookup(manifest, spec.manifest)
            if expected is None:
                problems.append(f"manifest has no {spec.manifest}")
            elif str(expected) != value:
                problems.append(f"label {value!r} but the image contains {expected!r}")
        checks.append(
            Check(f"label.{spec.key}", not problems, "; ".join(problems) if problems else value)
        )
    checks.extend(_common_checks(labels, standard))
    report = {
        "gate": "labels",
        "status": gate_status(checks),
        "standard": {"name": standard.name, "version": standard.version},
        "manifest_checked": manifest is not None,
        "checks": [c.as_dict() for c in checks],
    }
    return checks, report


# --- Static Dockerfile lint -------------------------------------------------

_ARG_REF = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)")


def _logical_lines(text: str) -> list[str]:
    """Join continuation lines and drop comments, as the Dockerfile parser does."""
    lines: list[str] = []
    buffer = ""
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("#") or (buffer and not stripped):
            continue
        if stripped.endswith("\\"):
            buffer += stripped[:-1] + " "
            continue
        buffer += stripped
        if buffer.strip():
            lines.append(buffer.strip())
        buffer = ""
    if buffer.strip():
        lines.append(buffer.strip())
    return lines


def parse_final_stage(dockerfile: str | Path) -> tuple[dict[str, str], set[str]]:
    """Return the LABELs and ARG names declared in the Dockerfile's final stage."""
    text = Path(dockerfile).read_text(encoding="utf-8")
    stage: list[str] = []
    for line in _logical_lines(text):
        if line.split(maxsplit=1)[0].upper() == "FROM":
            stage = []
        stage.append(line)
    if not stage:
        raise ConfigError(f"{dockerfile} has no FROM instruction")

    labels: dict[str, str] = {}
    args: set[str] = set()
    for line in stage:
        instruction, _, rest = line.partition(" ")
        instruction = instruction.upper()
        if instruction == "ARG":
            args.add(rest.split("=", 1)[0].strip())
        elif instruction == "LABEL":
            try:
                tokens = shlex.split(rest, posix=True)
            except ValueError as exc:
                raise ConfigError(f"cannot parse LABEL line: {exc}") from exc
            for token in tokens:
                if "=" not in token:
                    raise ConfigError(f"LABEL uses the legacy space-separated form: {token!r}")
                key, value = token.split("=", 1)
                labels[key] = value
    return labels, args


def lint_dockerfile(
    dockerfile: str | Path, standard: LabelStandard
) -> tuple[list[Check], dict[str, Any]]:
    """Fail before any build if the final stage does not declare every required label."""
    labels, args = parse_final_stage(dockerfile)
    checks: list[Check] = []
    for spec in standard.labels:
        if spec.key not in labels:
            checks.append(Check(f"declared.{spec.key}", False, "not declared in the final stage"))
            continue
        value = labels[spec.key]
        refs = _ARG_REF.findall(value)
        if refs:
            undeclared = [r for r in refs if r not in args]
            checks.append(
                Check(
                    f"declared.{spec.key}",
                    not undeclared,
                    f"from build arg {', '.join(refs)}"
                    if not undeclared
                    else f"uses {undeclared} with no ARG in the final stage",
                )
            )
            continue
        problems = []
        if spec.pattern is not None and not spec.pattern.match(value):
            problems.append(f"literal {value!r} does not match {spec.pattern_name}")
        if spec.allowed is not None and value not in spec.allowed:
            problems.append(f"literal {value!r} not in {list(spec.allowed)}")
        if spec.equals_standard_version and value != standard.version:
            problems.append(f"literal {value!r} but the standard is {standard.version}")
        checks.append(
            Check(f"declared.{spec.key}", not problems, "; ".join(problems) or f"literal {value!r}")
        )
    checks.extend(_common_checks(labels, standard))
    report = {
        "gate": "dockerfile-labels",
        "status": gate_status(checks),
        "dockerfile": str(dockerfile),
        "standard": {"name": standard.name, "version": standard.version},
        "checks": [c.as_dict() for c in checks],
    }
    return checks, report
