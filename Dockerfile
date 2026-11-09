# Validated data image.
#
# Three stages:
#   tooling   - a virtualenv with pinned dependencies and the dvgate validator
#   validate  - every gate runs here; a failing gate stops the build
#   runtime   - the shipped image, labelled, carrying only validated files
#
# The runtime stage copies its reports from `validate`, so BuildKit cannot
# skip the gates even when only the final stage is requested.
#
# Build with scripts/build-image.sh, which derives every label value from the
# files being shipped. Building by hand without those args fails in
# `verify-inputs`, because an empty label value is treated as an error.

# The base image is pinned by digest, so a moving tag cannot change the build
# silently. Dependabot opens a pull request when python:3.12-slim moves, and the
# change goes through the same gates as any other.

# ---------------------------------------------------------------- tooling ---
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS tooling
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /src
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY pyproject.toml README.md ./
COPY src/ src/
RUN pip install --no-cache-dir --no-deps . && dvgate --version

# --------------------------------------------------------------- validate ---
FROM tooling AS validate
ARG DATASET_RECORD=data/dataset.yaml
ARG DATASET_FILE=data/raw/penguins.csv
ARG DATA_CONTRACT=contracts/penguins.v1.yaml
ARG MODEL_CONTRACT=contracts/model.v1.yaml
ARG MODEL_DIR=model
ARG DATASET_NAME
ARG DATASET_VERSION
ARG DATASET_SHA256
ARG DATASET_SOURCE
ARG DATASET_REVISION
ARG DATASET_LICENCE
ARG SCHEMA_NAME
ARG SCHEMA_VERSION
ARG SCHEMA_SHA256
ARG MODEL_NAME
ARG MODEL_VERSION
ARG MODEL_SHA256
ARG LABEL_STANDARD_VERSION

WORKDIR /build
COPY standards/labels.v1.yaml standards/
COPY ${DATA_CONTRACT} ${MODEL_CONTRACT} contracts/
COPY ${DATASET_RECORD} data/dataset.yaml
COPY ${DATASET_FILE} data/dataset.csv
COPY ${MODEL_DIR}/ model/

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# Gate 1: the values the labels will carry must describe these exact bytes.
RUN dvgate verify-inputs \
        --record data/dataset.yaml --dataset data/dataset.csv \
        --contract "contracts/$(basename "${DATA_CONTRACT}")" \
        --model-contract "contracts/$(basename "${MODEL_CONTRACT}")" \
        --model-dir model --standard standards/labels.v1.yaml \
        --expect "dataset.name=${DATASET_NAME}" \
        --expect "dataset.version=${DATASET_VERSION}" \
        --expect "dataset.sha256=${DATASET_SHA256}" \
        --expect "dataset.source=${DATASET_SOURCE}" \
        --expect "dataset.revision=${DATASET_REVISION}" \
        --expect "dataset.licence=${DATASET_LICENCE}" \
        --expect "schema.name=${SCHEMA_NAME}" \
        --expect "schema.version=${SCHEMA_VERSION}" \
        --expect "schema.sha256=${SCHEMA_SHA256}" \
        --expect "model.name=${MODEL_NAME}" \
        --expect "model.version=${MODEL_VERSION}" \
        --expect "model.sha256=${MODEL_SHA256}" \
        --expect "label_standard.version=${LABEL_STANDARD_VERSION}"

# Gate 2: the dataset against its contract. Bad rows go to quarantine with a
# reason; dataset-level rules (schema, duplicates, quarantine share) fail the build.
RUN dvgate validate-data \
        --dataset data/dataset.csv \
        --contract "contracts/$(basename "${DATA_CONTRACT}")" \
        --report reports/data.json --quarantine reports/quarantine.csv

# Gate 3: the model artefact. Re-scored on the validated data, so the card cannot overstate it.
RUN dvgate validate-model \
        --dataset data/dataset.csv \
        --contract "contracts/$(basename "${DATA_CONTRACT}")" \
        --model-contract "contracts/$(basename "${MODEL_CONTRACT}")" \
        --model-dir model --report reports/model.json

# Record what passed, by hash, for the post-build label check and the runtime selfcheck.
RUN dvgate manifest \
        --record data/dataset.yaml --dataset data/dataset.csv \
        --contract "contracts/$(basename "${DATA_CONTRACT}")" \
        --model-contract "contracts/$(basename "${MODEL_CONTRACT}")" \
        --model-dir model --standard standards/labels.v1.yaml \
        --gate data=reports/data.json --gate model=reports/model.json \
        --file data/dataset.csv --file data/dataset.yaml \
        --file "contracts/$(basename "${DATA_CONTRACT}")" \
        --file "contracts/$(basename "${MODEL_CONTRACT}")" \
        --file model/model.json --file model/model_card.json \
        --file reports/data.json --file reports/model.json --file reports/quarantine.csv \
        --file standards/labels.v1.yaml \
        --out reports/manifest.json

# ---------------------------------------------------------------- runtime ---
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS runtime
ARG IMAGE_VERSION
ARG VCS_REF
ARG BUILD_DATE
ARG SOURCE_URL
ARG DATASET_NAME
ARG DATASET_VERSION
ARG DATASET_SHA256
ARG DATASET_SOURCE
ARG DATASET_REVISION
ARG DATASET_LICENCE
ARG SCHEMA_NAME
ARG SCHEMA_VERSION
ARG SCHEMA_SHA256
ARG MODEL_NAME
ARG MODEL_VERSION
ARG MODEL_SHA256
ARG LABEL_STANDARD_VERSION

LABEL org.opencontainers.image.title="dockerfile-data-validation" \
      org.opencontainers.image.description="Palmer Penguins dataset and species classifier, shipped only after data, schema and model gates pass" \
      org.opencontainers.image.version="${IMAGE_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.source="${SOURCE_URL}" \
      org.opencontainers.image.licenses="MIT AND CC0-1.0" \
      io.gbadedata.dataset.name="${DATASET_NAME}" \
      io.gbadedata.dataset.version="${DATASET_VERSION}" \
      io.gbadedata.dataset.sha256="${DATASET_SHA256}" \
      io.gbadedata.dataset.source="${DATASET_SOURCE}" \
      io.gbadedata.dataset.revision="${DATASET_REVISION}" \
      io.gbadedata.dataset.licence="${DATASET_LICENCE}" \
      io.gbadedata.schema.name="${SCHEMA_NAME}" \
      io.gbadedata.schema.version="${SCHEMA_VERSION}" \
      io.gbadedata.schema.sha256="${SCHEMA_SHA256}" \
      io.gbadedata.model.name="${MODEL_NAME}" \
      io.gbadedata.model.version="${MODEL_VERSION}" \
      io.gbadedata.model.sha256="${MODEL_SHA256}" \
      io.gbadedata.validation.report="/app/reports/manifest.json" \
      io.gbadedata.validation.policy="fail-closed" \
      io.gbadedata.label-standard.version="${LABEL_STANDARD_VERSION}"

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
# Apply Debian security updates so CVEs that already have a fix are not shipped
# (CI's Trivy step fails the build on any fixable HIGH or CRITICAL finding).
# Trade-off: this makes the OS layer depend on the build date. See README, Limits.
RUN apt-get update \
 && apt-get upgrade -y --no-install-recommends \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --system --gid 10001 dvgate \
 && useradd --system --uid 10001 --gid dvgate --no-create-home --shell /usr/sbin/nologin dvgate
WORKDIR /app
COPY --from=tooling /opt/venv /opt/venv
COPY --from=validate /build/data/ data/
COPY --from=validate /build/contracts/ contracts/
COPY --from=validate /build/model/ model/
COPY --from=validate /build/standards/ standards/
COPY --from=validate /build/reports/ reports/
USER 10001:10001

# Starting the container re-hashes every shipped file against the manifest.
ENTRYPOINT ["dvgate"]
CMD ["selfcheck", "--manifest", "/app/reports/manifest.json"]
