"""Keep the README's numbers true.

Every figure the README states is recomputed here from its source: the label
standard, the Dockerfile, the CI workflow, the data gate, the model card and the
test suite itself. Change any of them without updating the README and CI fails.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys

import pytest
import yaml
from conftest import DATA_CONTRACT, DATASET, DOCKERFILE, MODEL_DIR, ROOT, STANDARD

from dvgate.contracts import load_data_contract
from dvgate.data import validate_dataset
from dvgate.labels import load_standard, parse_final_stage

README = (ROOT / "README.md").read_text(encoding="utf-8")
VALIDATION_DOC = (ROOT / "docs" / "validation-logic.md").read_text(encoding="utf-8")
LABEL_DOC = (ROOT / "docs" / "label-standard.md").read_text(encoding="utf-8")


def flat(text: str) -> str:
    """README prose is wrapped; compare with whitespace collapsed."""
    return re.sub(r"\s+", " ", text)


def assert_stated(fragment: str, *documents: str) -> None:
    for doc in documents or (README,):
        assert flat(fragment) in flat(doc), f"README/docs should state: {fragment!r}"


def test_label_counts():
    standard = load_standard(STANDARD)
    keys = standard.keys
    oci = sum(k.startswith("org.opencontainers.") for k in keys)
    ours = sum(k.startswith(standard.namespace + ".") for k in keys)
    labels, _ = parse_final_stage(DOCKERFILE)
    from_args = sum("${" in labels[k] for k in keys)
    fixed = len(keys) - from_args
    namespace = standard.namespace
    assert_stated(
        f"{len(keys)} required labels: {oci} OCI keys and {ours} in the `{namespace}` namespace"
    )
    assert_stated(f"{from_args} from build args and {fixed} fixed in the Dockerfile")
    assert_stated(f"{from_args} of the {len(keys)}", LABEL_DOC)


def test_negative_test_count():
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    rejects = [
        step["name"]
        for job in workflow["jobs"].values()
        for step in job["steps"]
        if str(step.get("name", "")).startswith("Reject:")
    ]
    assert_stated(f"| **Negative tests** | {len(rejects)} deliberately broken inputs.")
    assert_stated(f"{len(rejects)} broken inputs")


def test_data_figures():
    result = validate_dataset(DATASET, load_data_contract(DATA_CONTRACT))
    rows = result.report["dataset"]["rows"]
    quarantined = result.report["quarantine"]["rows"]
    warned = result.report["warnings"]["by_reason"]["sex:missing"]
    assert_stated(f"{rows} rows, CC0. {quarantined} rows quarantined")
    assert_stated(f"{warned} kept with a warning")
    assert_stated(
        f"{rows} rows, {quarantined} quarantined (all four measurements missing), "
        f"{warned} kept with a `sex:missing` warning, {result.report['accepted_rows']} accepted",
        VALIDATION_DOC,
    )


def test_model_figures():
    card = json.loads((MODEL_DIR / "model_card.json").read_text())
    metrics = card["evaluation"]["metrics"]
    n_test = card["evaluation"]["n_test"]
    assert_stated(
        f"{metrics['accuracy']:.1%} accuracy and {metrics['macro_f1']:.3f} macro-F1 "
        f"on a {n_test}-row stratified holdout"
    )


def test_unit_test_count():
    out = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-o",
            "addopts=",
            str(ROOT / "tests"),
        ],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=True,
    ).stdout
    match = re.search(r"(\d+) tests? collected", out)
    if match is None:
        pytest.fail(f"could not read the collected test count:\n{out[-500:]}")
    collected = int(match.group(1))
    assert_stated(f"| **Unit tests** | {collected} |")
    assert_stated(f"pytest  # {collected} unit tests")
