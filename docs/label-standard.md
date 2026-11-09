# Image label standard (v1.0.0)

The machine-readable version is [`standards/labels.v1.yaml`](../standards/labels.v1.yaml).
This page explains the rules and the reasons for them.

## Principles

1. **Content labels are computed, never typed.** Every label that identifies what
   the image contains comes from the files being shipped (`dvgate build-args`) or
   from git and the clock (`scripts/build-image.sh`): 17 of the 22. The other five
   are descriptive fixed text in the Dockerfile, and the static lint checks them
   against their formats.
2. **Labels are checked twice.** Inside the build, `dvgate verify-inputs` stops the
   build if a build arg does not describe the bytes in the build context. After the
   build, `dvgate check-labels` compares the image's labels with the manifest written
   inside the image, which catches a label changed with `docker build --label` or
   any later re-tagging tool.
3. **OCI keys first.** The standard uses the pre-defined
   `org.opencontainers.image.*` annotation keys wherever one fits, and a single
   reverse-DNS namespace, `io.gbadedata`, for everything else.
4. **Labels are public.** Anyone who can pull the image can read them, so the
   standard rejects any key or value that looks like a credential.

## Required labels

| Label | Format | Source | Must equal manifest |
|---|---|---|---|
| `org.opencontainers.image.title` | non-empty | Dockerfile literal | |
| `org.opencontainers.image.description` | non-empty | Dockerfile literal | |
| `org.opencontainers.image.version` | semver | `pyproject.toml` | |
| `org.opencontainers.image.revision` | 40-char git SHA | `git rev-parse HEAD` | |
| `org.opencontainers.image.created` | RFC 3339, UTC | build time | |
| `org.opencontainers.image.source` | https URL | git remote | |
| `org.opencontainers.image.licenses` | SPDX expression | Dockerfile literal | |
| `io.gbadedata.dataset.name` | non-empty | `data/dataset.yaml` | `dataset.name` |
| `io.gbadedata.dataset.version` | calver or semver | `data/dataset.yaml` | `dataset.version` |
| `io.gbadedata.dataset.sha256` | 64 hex | hash of the dataset file | `dataset.sha256` |
| `io.gbadedata.dataset.source` | https URL | `data/dataset.yaml` | `dataset.source` |
| `io.gbadedata.dataset.revision` | 40-char git SHA | upstream commit | `dataset.revision` |
| `io.gbadedata.dataset.licence` | SPDX | `data/dataset.yaml` | `dataset.licence` |
| `io.gbadedata.schema.name` | non-empty | data contract | `schema.name` |
| `io.gbadedata.schema.version` | semver | data contract | `schema.version` |
| `io.gbadedata.schema.sha256` | 64 hex | hash of the data contract | `schema.sha256` |
| `io.gbadedata.model.name` | non-empty | `model/model.json` | `model.name` |
| `io.gbadedata.model.version` | semver | `model/model.json` | `model.version` |
| `io.gbadedata.model.sha256` | 64 hex | hash of `model.json` | `model.sha256` |
| `io.gbadedata.validation.report` | absolute path | Dockerfile literal | |
| `io.gbadedata.validation.policy` | `fail-closed` | Dockerfile literal | |
| `io.gbadedata.label-standard.version` | semver, equal to this standard | standard file | |

## Rules beyond the list

- **Unknown keys in the namespace fail.** `io.gbadedata.dataset.sha265` is a typo, and
  a typo that passes silently leaves the real label missing in practice.
- **No credential-like content.** Keys and values are matched against
  `secret_pattern` (password, secret, token, API key, private key).
- **Key=value syntax only.** The static lint rejects the legacy
  `LABEL key value` form, which cannot hold more than one label per line and
  is easy to misread.

## When the standard changes

Bump `version` in the YAML. Every image built afterwards must carry the new
version in `io.gbadedata.label-standard.version`, so a consumer can tell which
rules an image was checked against. Removing a required label is a major
version change; adding one is minor.

## Reading the labels

```bash
docker image inspect ghcr.io/gbadedata/dockerfile-data-validation:latest \
  --format '{{ json .Config.Labels }}' | python -m json.tool
```
