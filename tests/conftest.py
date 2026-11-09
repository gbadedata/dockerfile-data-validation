from __future__ import annotations

import csv
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "data" / "raw" / "penguins.csv"
DATA_CONTRACT = ROOT / "contracts" / "penguins.v1.yaml"
MODEL_CONTRACT = ROOT / "contracts" / "model.v1.yaml"
STANDARD = ROOT / "standards" / "labels.v1.yaml"
RECORD = ROOT / "data" / "dataset.yaml"
MODEL_DIR = ROOT / "model"
DOCKERFILE = ROOT / "Dockerfile"


@pytest.fixture
def rows() -> list[list[str]]:
    """The real dataset as a list of rows (header first), for tests to edit."""
    with DATASET.open(newline="", encoding="utf-8") as handle:
        return list(csv.reader(handle))


@pytest.fixture
def write_csv(tmp_path):
    def _write(rows: list[list[str]], name: str = "data.csv") -> Path:
        path = tmp_path / name
        with path.open("w", newline="", encoding="utf-8") as handle:
            csv.writer(handle, lineterminator="\n").writerows(rows)
        return path

    return _write


@pytest.fixture
def model_copy(tmp_path) -> Path:
    target = tmp_path / "model"
    shutil.copytree(MODEL_DIR, target)
    return target


def set_cell(rows: list[list[str]], row: int, column: str, value: str) -> None:
    rows[row][rows[0].index(column)] = value
