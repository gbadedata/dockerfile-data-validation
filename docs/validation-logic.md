# Validation logic

Every gate is a `dvgate` command. It prints one line per check, writes a JSON
report, and exits with a code a build or scheduler can act on:

| Exit code | Meaning |
|---|---|
| 0 | every check passed (warnings may be recorded) |
| 1 | configuration or input error: a contract, record or path is wrong |
| 2 | a gate failed: the data, model or labels broke a rule |

Keeping 1 and 2 apart matters: a typo in a contract should not be reported as
bad data, and bad data should never be mistaken for a broken pipeline.

## Gate 1: build inputs (`verify-inputs`, inside the build)

Runs first in the `validate` stage. It recomputes the identity of every input
from the bytes in the build context and compares it with the build args the
labels will be made from.

| Check | Fails when |
|---|---|
| `provenance.dataset_sha256` | the dataset does not hash to the value in its provenance record |
| `provenance.model_identity` | `model.json` names a different model or version than the model contract |
| `build_arg.<key>` | a build arg is empty, or differs from the value computed from the file |

## Gate 2: dataset (`validate-data`)

The CSV is read with every cell as text, so type problems are found by the
rules rather than hidden by pandas' type inference.

**Schema checks** (fail the gate): required columns present; no extra columns
when the contract says `extra_columns: reject`.

**Row rules** quarantine a row with a reason code instead of failing the build.
A row can collect several codes:

| Reason code | Meaning |
|---|---|
| `<column>:missing` | an NA token (`NA` or empty) in a column whose `missing` policy is `quarantine` |
| `<column>:type` | not a number, or not a whole number in an integer column |
| `<column>:out_of_range` | outside the contract's `range` |
| `<column>:not_allowed` | not in the contract's `allowed` list |

A `missing: warn` column (here, `sex`) is counted in the report but the row is kept.

**Dataset rules** (fail the gate):

| Check | Rule in this contract |
|---|---|
| `dataset.row_count` | 300 to 400 rows |
| `dataset.duplicate_rows` | no exact duplicate rows |
| `dataset.quarantine_fraction` | at most 2% of rows quarantined |
| `dataset.categories.<col>` | every allowed species and island still present after quarantine |
| `dataset.min_share.species` | the smallest species is at least 15% of accepted rows |

The quarantine limit is what turns row problems into a build failure: a few bad
rows are isolated and reported, but a file where many rows are bad is a broken
delivery, not a dataset with outliers.

On the real dataset: 344 rows, 2 quarantined (all four measurements missing),
11 kept with a `sex:missing` warning, 342 accepted.

## Gate 3: model artefact (`validate-model`)

| Check | Fails when |
|---|---|
| `artefact.files` | `model.json` or `model_card.json` is missing |
| `artefact.format`, `artefact.identity` | wrong format, name or version |
| `schema.features` | the model's features differ from the contract's, in name or order |
| `schema.features_in_data_contract` | a feature is not a numeric column in the data contract |
| `schema.classes` | the model's classes differ from the target column's allowed values |
| `artefact.parameters` | wrong shapes, NaN or infinite values, or a non-positive scale |
| `lineage.dataset_sha256` | the model was trained on a different dataset file |
| `lineage.data_contract_version` | the model was trained under a different contract version |
| `evaluation.split` | the card's holdout seed or fraction differs from the contract's |
| `evaluation.<metric>` | the re-scored metric is below the contract's threshold |
| `card.<metric>` | the card's number differs from the re-scored number by more than 1e-6 |

The model card is never taken on trust. The gate rebuilds the stratified holdout
from the validated data with the contract's seed, scores the model again, and
compares.

## Gate 4: labels (`lint-dockerfile` before the build, `check-labels` after)

`lint-dockerfile` parses the Dockerfile's final stage and fails if a required
label is not declared, if a label uses a build arg the stage never declares, or
if a literal value breaks its format.

`check-labels` reads the labels from `docker image inspect` and the manifest
from inside the image. See [label-standard.md](label-standard.md).

## Gate 5: runtime selfcheck (`selfcheck`, the image's default command)

Re-hashes every file listed in `/app/reports/manifest.json` and confirms each
build gate is recorded as passed. Run it as an init container, or before a job
reads the data, to catch a file changed after the image was built.
