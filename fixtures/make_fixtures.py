"""Regenerate the deliberately broken inputs that CI must reject.

Run from the repository root:  python fixtures/make_fixtures.py
Each fixture comes with its own provenance record, so the build gets past the
hash checks and fails at the gate the fixture is meant to exercise.
"""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "data" / "raw" / "penguins.csv"
OUT = ROOT / "fixtures"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_record(folder: Path, data_file: Path, note: str) -> None:
    record = (ROOT / "data" / "dataset.yaml").read_text(encoding="utf-8").splitlines()
    lines = [f"# FIXTURE: {note}"]
    for line in record:
        if line.startswith("file:"):
            line = f"file: {data_file.relative_to(ROOT).as_posix()}"
        elif line.startswith("sha256:"):
            line = f"sha256: {sha256(data_file)}"
        lines.append(line)
    (folder / "dataset.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def bad_values() -> None:
    folder = OUT / "bad-values"
    folder.mkdir(parents=True, exist_ok=True)
    with SOURCE.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    header, body = rows[0], rows[1:]
    col = {name: i for i, name in enumerate(header)}
    edits = [
        (10, "species", "Emperor"),
        (20, "species", "Emperor"),
        (30, "island", "Anvers"),
        (40, "body_mass_g", "45000"),
        (50, "body_mass_g", "-1"),
        (60, "flipper_length_mm", "abc"),
        (70, "bill_length_mm", "4.21e3"),
        (80, "year", "2019"),
    ]
    for row_index, column, value in edits:
        body[row_index][col[column]] = value
    body += [body[100], body[101]]  # two exact duplicate rows
    data_file = folder / "penguins.csv"
    with data_file.open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle, lineterminator="\n").writerows([header, *body])
    write_record(
        folder, data_file, "8 invalid values and 2 duplicate rows; the data gate must fail"
    )


def missing_column() -> None:
    folder = OUT / "missing-column"
    folder.mkdir(parents=True, exist_ok=True)
    with SOURCE.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    drop = rows[0].index("island")
    data_file = folder / "penguins.csv"
    with data_file.open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle, lineterminator="\n").writerows(
            [[v for i, v in enumerate(r) if i != drop] for r in rows]
        )
    write_record(folder, data_file, "the island column is missing; the schema check must fail")


def inflated_card() -> None:
    folder = OUT / "model-inflated-card"
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / "model" / "model.json", folder / "model.json")
    card = json.loads((ROOT / "model" / "model_card.json").read_text(encoding="utf-8"))
    card["evaluation"]["metrics"]["accuracy"] = 0.999
    (folder / "model_card.json").write_text(json.dumps(card, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    bad_values()
    missing_column()
    inflated_card()
    print("fixtures written to", OUT)
