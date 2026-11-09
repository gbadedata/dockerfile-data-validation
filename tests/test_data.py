from __future__ import annotations

from dataclasses import replace

import pytest
from conftest import DATA_CONTRACT, DATASET, set_cell

from dvgate.common import ConfigError
from dvgate.contracts import load_data_contract
from dvgate.data import validate_dataset


@pytest.fixture(scope="module")
def contract():
    return load_data_contract(DATA_CONTRACT)


def failed(result) -> set[str]:
    return {c.id for c in result.checks if not c.passed and c.severity == "error"}


def test_real_dataset_passes_with_two_rows_quarantined(contract):
    result = validate_dataset(DATASET, contract)
    assert result.passed
    assert result.report["quarantine"]["rows"] == 2
    assert result.report["accepted_rows"] == 342
    assert result.report["warnings"]["by_reason"] == {"sex:missing": 11}
    assert list(result.quarantined["_line"]) == [5, 273]


def test_missing_column_fails_schema(contract, rows, write_csv):
    drop = rows[0].index("island")
    path = write_csv([[v for i, v in enumerate(r) if i != drop] for r in rows])
    result = validate_dataset(path, contract)
    assert "schema.required_columns" in failed(result)


def test_extra_column_is_rejected(contract, rows, write_csv):
    path = write_csv([r + (["notes"] if i == 0 else [""]) for i, r in enumerate(rows)])
    assert "schema.extra_columns" in failed(validate_dataset(path, contract))


def test_extra_column_allowed_when_contract_says_so(contract, rows, write_csv):
    path = write_csv([r + (["notes"] if i == 0 else [""]) for i, r in enumerate(rows)])
    relaxed = replace(contract, extra_columns="allow")
    assert validate_dataset(path, relaxed).passed


@pytest.mark.parametrize(
    "column, value, reason",
    [
        ("species", "Emperor", "species:not_allowed"),
        ("flipper_length_mm", "abc", "flipper_length_mm:type"),
        ("flipper_length_mm", "190.5", "flipper_length_mm:type"),
        ("body_mass_g", "45000", "body_mass_g:out_of_range"),
        ("year", "2019", "year:out_of_range"),
        ("island", "NA", "island:missing"),
    ],
)
def test_bad_value_is_quarantined_with_reason(contract, rows, write_csv, column, value, reason):
    set_cell(rows, 10, column, value)
    result = validate_dataset(write_csv(rows), contract)
    assert result.passed  # one bad row is within the 2% quarantine allowance
    assert reason in result.report["quarantine"]["by_reason"]
    line_11 = result.quarantined[result.quarantined["_line"] == 11]
    assert reason in line_11["_reasons"].iloc[0]


def test_too_many_bad_rows_fails_the_gate(contract, rows, write_csv):
    for r in range(1, 12):
        set_cell(rows, r, "body_mass_g", "1")
    assert "dataset.quarantine_fraction" in failed(validate_dataset(write_csv(rows), contract))


def test_duplicate_rows_fail(contract, rows, write_csv):
    rows.append(list(rows[50]))
    assert "dataset.duplicate_rows" in failed(validate_dataset(write_csv(rows), contract))


def test_row_count_bounds(contract, rows, write_csv):
    assert "dataset.row_count" in failed(validate_dataset(write_csv(rows[:100]), contract))


def test_category_lost_to_quarantine_fails(contract, rows, write_csv):
    species = rows[0].index("species")
    for r in rows[1:]:
        if r[species] == "Chinstrap":
            r[rows[0].index("year")] = "1990"
    result = validate_dataset(write_csv(rows), contract)
    assert "dataset.categories.species" in failed(result)


def test_unparseable_file_fails_readable(contract, tmp_path):
    path = tmp_path / "broken.csv"
    path.write_bytes(b"species,island\n\xff\xfe\x00garbage\n")
    assert "file.readable" in failed(validate_dataset(path, contract))


def test_contract_typo_is_a_config_error(tmp_path):
    text = DATA_CONTRACT.read_text().replace("type: float", "type: flaot", 1)
    bad = tmp_path / "contract.yaml"
    bad.write_text(text)
    with pytest.raises(ConfigError, match="unknown type 'flaot'"):
        load_data_contract(bad)


@pytest.mark.parametrize(
    "old, new, message",
    [
        ("version: 1.0.0", "version: one", "not semver"),
        ("  max_duplicate_rows: 0", "  max_dupes: 0", "unknown dataset rules"),
        ("    missing: warn", "    missing: ignore", "unknown missing policy"),
    ],
)
def test_contract_structure_is_checked(tmp_path, old, new, message):
    bad = tmp_path / "contract.yaml"
    bad.write_text(DATA_CONTRACT.read_text().replace(old, new, 1))
    with pytest.raises(ConfigError, match=message):
        load_data_contract(bad)
