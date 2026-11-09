"""Command line entry point: `dvgate <command>`.

Exit codes: 0 every gate passed, 2 a gate failed, 1 configuration or input error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import EXIT_CONFIG_ERROR, EXIT_GATE_FAILED, EXIT_OK, __version__
from .common import Check, ConfigError, gate_status, print_checks, sha256_file, write_json
from .contracts import load_data_contract, load_dataset_record, load_model_contract
from .data import validate_dataset
from .labels import check_labels, lint_dockerfile, load_standard
from .manifest import describe_inputs, selfcheck, verify_inputs, write_manifest
from .model import train, validate_model


def _exit_for(checks: list[Check]) -> int:
    return EXIT_OK if gate_status(checks) == "passed" else EXIT_GATE_FAILED


def cmd_validate_data(args: argparse.Namespace) -> int:
    contract = load_data_contract(args.contract)
    result = validate_dataset(args.dataset, contract)
    print_checks(f"data gate ({contract.name} {contract.version})", result.checks)
    if args.report:
        write_json(args.report, result.report)
    if args.quarantine:
        Path(args.quarantine).parent.mkdir(parents=True, exist_ok=True)
        result.quarantined.to_csv(args.quarantine, index=False)
    return _exit_for(result.checks)


def cmd_train(args: argparse.Namespace) -> int:
    data_contract = load_data_contract(args.contract)
    model_contract = load_model_contract(args.model_contract)
    result = validate_dataset(args.dataset, data_contract)
    if not result.passed:
        print_checks("data gate", result.checks)
        print("refusing to train on data that fails its contract", file=sys.stderr)
        return EXIT_GATE_FAILED
    card = train(
        result.accepted, model_contract, data_contract, sha256_file(args.dataset), args.out
    )
    print(json.dumps(card["evaluation"], indent=2))
    return EXIT_OK


def cmd_validate_model(args: argparse.Namespace) -> int:
    data_contract = load_data_contract(args.contract)
    model_contract = load_model_contract(args.model_contract)
    data = validate_dataset(args.dataset, data_contract)
    if not data.passed:
        print_checks("data gate", data.checks)
        return EXIT_GATE_FAILED
    checks, report = validate_model(
        args.model_dir, model_contract, data_contract, data.accepted, sha256_file(args.dataset)
    )
    print_checks(f"model gate ({model_contract.name} {model_contract.version})", checks)
    if args.report:
        write_json(args.report, report)
    return _exit_for(checks)


def cmd_lint_dockerfile(args: argparse.Namespace) -> int:
    standard = load_standard(args.standard)
    checks, report = lint_dockerfile(args.dockerfile, standard)
    print_checks(f"Dockerfile label lint ({standard.name} {standard.version})", checks)
    if args.report:
        write_json(args.report, report)
    return _exit_for(checks)


def _read_labels(path: str) -> dict[str, str]:
    data = json.loads(Path(path).read_text(encoding="utf-8") or "null")
    if isinstance(data, list):  # raw `docker image inspect` output
        data = data[0].get("Config", {}).get("Labels") if data else None
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path} does not contain a label mapping")
    return {str(k): str(v) for k, v in data.items()}


def cmd_check_labels(args: argparse.Namespace) -> int:
    standard = load_standard(args.standard)
    labels = _read_labels(args.labels)
    manifest = json.loads(Path(args.manifest).read_text()) if args.manifest else None
    checks, report = check_labels(labels, standard, manifest)
    print_checks(f"image labels ({standard.name} {standard.version})", checks)
    if args.report:
        write_json(args.report, report)
    return _exit_for(checks)


def _inputs(args: argparse.Namespace):
    record = load_dataset_record(args.record)
    dataset = Path(args.dataset or record["file"])
    if not dataset.is_file():
        raise ConfigError(f"dataset file not found: {dataset}")
    data_contract = load_data_contract(args.contract)
    model_contract = load_model_contract(args.model_contract)
    standard = load_standard(args.standard)
    identity = describe_inputs(
        record, dataset, data_contract, model_contract, Path(args.model_dir), standard
    )
    return record, dataset, model_contract, identity


def cmd_build_args(args: argparse.Namespace) -> int:
    """Print KEY=VALUE build args, every value computed from the files themselves."""
    record, dataset, _, identity = _inputs(args)
    values = {
        "DATASET_RECORD": args.record,
        "DATASET_FILE": str(dataset),
        "DATA_CONTRACT": args.contract,
        "MODEL_CONTRACT": args.model_contract,
        "MODEL_DIR": args.model_dir,
        "DATASET_NAME": identity["dataset"]["name"],
        "DATASET_VERSION": identity["dataset"]["version"],
        "DATASET_SHA256": identity["dataset"]["sha256"],
        "DATASET_SOURCE": identity["dataset"]["source"],
        "DATASET_REVISION": identity["dataset"]["revision"],
        "DATASET_LICENCE": identity["dataset"]["licence"],
        "SCHEMA_NAME": identity["schema"]["name"],
        "SCHEMA_VERSION": identity["schema"]["version"],
        "SCHEMA_SHA256": identity["schema"]["sha256"],
        "MODEL_NAME": identity["model"]["name"],
        "MODEL_VERSION": identity["model"]["version"],
        "MODEL_SHA256": identity["model"]["sha256"],
        "LABEL_STANDARD_VERSION": identity["label_standard"]["version"],
        "IMAGE_VERSION": __version__,
    }
    for key, value in values.items():
        if "\n" in value:
            raise ConfigError(f"{key} contains a newline")
        print(f"{key}={value}")
    return EXIT_OK


def _parse_expectations(pairs: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise ConfigError(f"--expect needs KEY=VALUE, got {pair!r}")
        key, value = pair.split("=", 1)
        out[key.strip()] = value.strip()
    return out


def cmd_verify_inputs(args: argparse.Namespace) -> int:
    record, _, model_contract, identity = _inputs(args)
    checks = verify_inputs(identity, record, model_contract, _parse_expectations(args.expect))
    print_checks("build inputs", checks)
    return _exit_for(checks)


def cmd_manifest(args: argparse.Namespace) -> int:
    _, _, _, identity = _inputs(args)
    gates: dict[str, str] = {}
    for pair in args.gate:
        name, _, report_path = pair.partition("=")
        report = json.loads(Path(report_path).read_text(encoding="utf-8"))
        gates[name] = report.get("status", "unknown")
    if any(status != "passed" for status in gates.values()):
        print(f"refusing to write a manifest for failed gates: {gates}", file=sys.stderr)
        return EXIT_GATE_FAILED
    manifest = write_manifest(identity, Path(args.root), args.file, gates, Path(args.out))
    print(f"manifest: {len(manifest['files'])} files, gates {gates}")
    return EXIT_OK


def cmd_selfcheck(args: argparse.Namespace) -> int:
    checks, result = selfcheck(Path(args.manifest))
    manifest = result["manifest"]
    print(
        f"dataset {manifest['dataset']['name']} {manifest['dataset']['version']} | "
        f"schema {manifest['schema']['name']} {manifest['schema']['version']} | "
        f"model {manifest['model']['name']} {manifest['model']['version']}"
    )
    print_checks("selfcheck", checks)
    return _exit_for(checks)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dvgate", description=__doc__)
    parser.add_argument("--version", action="version", version=f"dvgate {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    def inputs(p: argparse.ArgumentParser) -> None:
        p.add_argument("--record", default="data/dataset.yaml")
        p.add_argument("--dataset", help="defaults to the file named in the record")
        p.add_argument("--contract", default="contracts/penguins.v1.yaml")
        p.add_argument("--model-contract", default="contracts/model.v1.yaml")
        p.add_argument("--model-dir", default="model")
        p.add_argument("--standard", default="standards/labels.v1.yaml")

    p = sub.add_parser("validate-data", help="run the dataset gate")
    p.add_argument("--dataset", required=True)
    p.add_argument("--contract", required=True)
    p.add_argument("--report")
    p.add_argument("--quarantine")
    p.set_defaults(func=cmd_validate_data)

    p = sub.add_parser("train", help="train the model artefact on validated data")
    p.add_argument("--dataset", required=True)
    p.add_argument("--contract", required=True)
    p.add_argument("--model-contract", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_train)

    p = sub.add_parser("validate-model", help="run the model artefact gate")
    p.add_argument("--dataset", required=True)
    p.add_argument("--contract", required=True)
    p.add_argument("--model-contract", required=True)
    p.add_argument("--model-dir", required=True)
    p.add_argument("--report")
    p.set_defaults(func=cmd_validate_model)

    p = sub.add_parser("lint-dockerfile", help="check the final stage declares every label")
    p.add_argument("--dockerfile", default="Dockerfile")
    p.add_argument("--standard", default="standards/labels.v1.yaml")
    p.add_argument("--report")
    p.set_defaults(func=cmd_lint_dockerfile)

    p = sub.add_parser("check-labels", help="verify a built image's labels")
    p.add_argument("--labels", required=True, help="JSON label map or docker inspect output")
    p.add_argument("--standard", default="standards/labels.v1.yaml")
    p.add_argument("--manifest", help="manifest.json copied out of the image")
    p.add_argument("--report")
    p.set_defaults(func=cmd_check_labels)

    p = sub.add_parser("build-args", help="print docker build args derived from the inputs")
    inputs(p)
    p.set_defaults(func=cmd_build_args)

    p = sub.add_parser("verify-inputs", help="check build args against the files (in-build)")
    inputs(p)
    p.add_argument("--expect", action="append", default=[], metavar="KEY=VALUE")
    p.set_defaults(func=cmd_verify_inputs)

    p = sub.add_parser("manifest", help="write the validation manifest (in-build)")
    inputs(p)
    p.add_argument("--root", default=".")
    p.add_argument("--file", action="append", default=[], help="file to record, relative to root")
    p.add_argument("--gate", action="append", default=[], metavar="NAME=REPORT.json")
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_manifest)

    p = sub.add_parser("selfcheck", help="re-hash shipped files against the manifest")
    p.add_argument("--manifest", default="/app/reports/manifest.json")
    p.set_defaults(func=cmd_selfcheck)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
