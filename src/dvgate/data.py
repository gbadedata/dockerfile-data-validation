"""Dataset gate: schema, row-level rules with reason-coded quarantine, dataset rules."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .common import Check, gate_status, sha256_file
from .contracts import DataContract


@dataclass
class DataResult:
    checks: list[Check]
    report: dict[str, Any]
    accepted: pd.DataFrame  # typed rows that passed every row rule
    quarantined: pd.DataFrame  # original values plus _line and _reasons

    @property
    def passed(self) -> bool:
        return self.report["status"] == "passed"


def read_raw(path: str | Path, contract: DataContract) -> pd.DataFrame:
    """Read every cell as text, so type problems are found by the rules, not by pandas."""
    return pd.read_csv(
        path,
        dtype=str,
        keep_default_na=False,
        na_filter=False,
        sep=contract.delimiter,
        encoding=contract.encoding,
    )


def _numeric(series: pd.Series, is_na: pd.Series) -> tuple[pd.Series, pd.Series]:
    values = pd.to_numeric(series.where(~is_na), errors="coerce").astype("float64")
    bad_type = (~is_na) & values.isna()
    return values, bad_type


def validate_dataset(path: str | Path, contract: DataContract) -> DataResult:
    path = Path(path)
    checks: list[Check] = []
    dataset_info: dict[str, Any] = {"path": str(path), "sha256": sha256_file(path)}

    try:
        raw = read_raw(path, contract)
    except (pd.errors.ParserError, UnicodeDecodeError, ValueError) as exc:
        checks.append(Check("file.readable", False, f"could not parse as CSV: {exc}"))
        return _finish(checks, dataset_info, contract, None, None, {}, {})
    checks.append(Check("file.readable", True, f"parsed {len(raw)} rows"))
    dataset_info.update(rows=int(len(raw)), columns=list(raw.columns))

    expected = contract.column_names
    missing_cols = [c for c in expected if c not in raw.columns]
    extra_cols = [c for c in raw.columns if c not in expected]
    checks.append(
        Check(
            "schema.required_columns",
            not missing_cols,
            "all contract columns present" if not missing_cols else f"missing: {missing_cols}",
        )
    )
    if contract.extra_columns == "reject":
        checks.append(
            Check(
                "schema.extra_columns",
                not extra_cols,
                "no columns outside the contract"
                if not extra_cols
                else f"unexpected: {extra_cols}",
            )
        )
    if missing_cols:
        return _finish(checks, dataset_info, contract, None, None, {}, {})

    na_tokens = set(contract.na_values)
    quarantine_masks: dict[str, pd.Series] = {}
    warning_masks: dict[str, pd.Series] = {}
    typed = pd.DataFrame(index=raw.index)

    for col in contract.columns:
        series = raw[col.name].str.strip()
        is_na = series.isin(na_tokens)

        if col.missing == "quarantine":
            quarantine_masks[f"{col.name}:missing"] = is_na
        elif col.missing == "warn":
            warning_masks[f"{col.name}:missing"] = is_na

        if col.type == "string":
            typed[col.name] = series.where(~is_na, None)
            if col.allowed is not None:
                quarantine_masks[f"{col.name}:not_allowed"] = (~is_na) & ~series.isin(col.allowed)
            continue

        values, bad_type = _numeric(series, is_na)
        if col.type == "integer":
            not_whole = values.notna() & (np.floor(values) != values)
            bad_type = bad_type | not_whole
        quarantine_masks[f"{col.name}:type"] = bad_type
        if col.range is not None:
            low, high = col.range
            quarantine_masks[f"{col.name}:out_of_range"] = values.notna() & (
                (values < low) | (values > high)
            )
        typed[col.name] = values.where(~bad_type)

    quarantine_masks = {k: m for k, m in quarantine_masks.items() if m.any()}
    warning_masks = {k: m for k, m in warning_masks.items() if m.any()}

    in_quarantine = pd.Series(False, index=raw.index)
    for mask in quarantine_masks.values():
        in_quarantine |= mask

    reasons = pd.Series([""] * len(raw), index=raw.index, dtype=object)
    for code, mask in quarantine_masks.items():
        reasons[mask] = reasons[mask].map(lambda r, c=code: f"{r};{c}" if r else c)

    quarantined = raw[in_quarantine].copy()
    quarantined.insert(0, "_line", quarantined.index + 2)  # header is line 1
    quarantined["_reasons"] = reasons[in_quarantine]
    accepted = typed[~in_quarantine].reset_index(drop=True)

    by_reason = {k: int(m.sum()) for k, m in sorted(quarantine_masks.items())}
    warn_by_reason = {k: int(m.sum()) for k, m in sorted(warning_masks.items())}
    checks.append(
        Check(
            "rows.quarantine",
            True,
            f"{int(in_quarantine.sum())} of {len(raw)} rows quarantined"
            + (f" ({', '.join(f'{k}={v}' for k, v in by_reason.items())})" if by_reason else ""),
            severity="warn",
        )
    )
    if warn_by_reason:
        checks.append(
            Check(
                "rows.warnings",
                False,
                ", ".join(f"{k}={v}" for k, v in warn_by_reason.items()) + " (rows kept)",
                severity="warn",
            )
        )

    checks.extend(_dataset_rules(raw, accepted, in_quarantine, contract))
    return _finish(checks, dataset_info, contract, accepted, quarantined, by_reason, warn_by_reason)


def _dataset_rules(
    raw: pd.DataFrame, accepted: pd.DataFrame, in_quarantine: pd.Series, contract: DataContract
) -> list[Check]:
    rules = contract.dataset_rules
    out: list[Check] = []
    total = len(raw)

    if "row_count" in rules:
        low = rules["row_count"].get("min", 0)
        high = rules["row_count"].get("max", float("inf"))
        out.append(
            Check(
                "dataset.row_count",
                low <= total <= high,
                f"{total} rows (allowed {low} to {high})",
            )
        )
    if "max_duplicate_rows" in rules:
        dupes = int(raw.duplicated().sum())
        limit = int(rules["max_duplicate_rows"])
        out.append(
            Check(
                "dataset.duplicate_rows", dupes <= limit, f"{dupes} duplicate rows (limit {limit})"
            )
        )
    if "max_quarantine_fraction" in rules:
        fraction = float(in_quarantine.mean()) if total else 1.0
        limit = float(rules["max_quarantine_fraction"])
        out.append(
            Check(
                "dataset.quarantine_fraction",
                fraction <= limit,
                f"{fraction:.2%} of rows quarantined (limit {limit:.2%})",
            )
        )
    for col_name in rules.get("require_all_categories", []):
        allowed = set(contract.column(col_name).allowed or ())
        present = set(accepted[col_name].dropna().unique()) if len(accepted) else set()
        absent = sorted(allowed - present)
        out.append(
            Check(
                f"dataset.categories.{col_name}",
                not absent,
                "every allowed value present"
                if not absent
                else f"absent after quarantine: {absent}",
            )
        )
    if "min_category_share" in rules:
        col_name = rules["min_category_share"]["column"]
        floor = float(rules["min_category_share"]["min"])
        shares = accepted[col_name].value_counts(normalize=True) if len(accepted) else pd.Series()
        smallest = float(shares.min()) if len(shares) else 0.0
        label = shares.idxmin() if len(shares) else "none"
        out.append(
            Check(
                f"dataset.min_share.{col_name}",
                smallest >= floor,
                f"smallest class {label} is {smallest:.1%} of accepted rows (floor {floor:.0%})",
            )
        )
    return out


def _finish(
    checks: list[Check],
    dataset_info: dict[str, Any],
    contract: DataContract,
    accepted: pd.DataFrame | None,
    quarantined: pd.DataFrame | None,
    by_reason: dict[str, int],
    warn_by_reason: dict[str, int],
) -> DataResult:
    accepted = accepted if accepted is not None else pd.DataFrame(columns=contract.column_names)
    quarantined = quarantined if quarantined is not None else pd.DataFrame()
    report = {
        "gate": "data",
        "status": gate_status(checks),
        "dataset": dataset_info,
        "contract": {"name": contract.name, "version": contract.version, "sha256": contract.sha256},
        "accepted_rows": int(len(accepted)),
        "quarantine": {"rows": int(len(quarantined)), "by_reason": by_reason},
        "warnings": {"by_reason": warn_by_reason},
        "checks": [c.as_dict() for c in checks],
    }
    return DataResult(checks=checks, report=report, accepted=accepted, quarantined=quarantined)
