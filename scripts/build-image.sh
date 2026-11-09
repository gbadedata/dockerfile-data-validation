#!/usr/bin/env bash
# Build the image with every label value derived from the files being shipped.
#
# Usage: scripts/build-image.sh IMAGE_TAG [-- extra docker build args]
#
# Inputs can be swapped with environment variables, which is how CI builds the
# deliberately broken fixtures:
#   DATASET_RECORD  (default data/dataset.yaml)   provenance record; names the dataset file
#   DATA_CONTRACT   (default contracts/penguins.v1.yaml)
#   MODEL_CONTRACT  (default contracts/model.v1.yaml)
#   MODEL_DIR       (default model)
#   SOURCE_URL      (default derived from the git remote)
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 IMAGE_TAG [-- extra docker build args]" >&2
  exit 64
fi
image="$1"
shift
if [[ "${1:-}" == "--" ]]; then shift; fi

cd "$(dirname "$0")/.."

record="${DATASET_RECORD:-data/dataset.yaml}"
data_contract="${DATA_CONTRACT:-contracts/penguins.v1.yaml}"
model_contract="${MODEL_CONTRACT:-contracts/model.v1.yaml}"
model_dir="${MODEL_DIR:-model}"

# Values that describe the build itself rather than its inputs.
vcs_ref="$(git rev-parse HEAD 2>/dev/null || true)"
if [[ ! "$vcs_ref" =~ ^[0-9a-f]{40}$ ]]; then
  echo "error: not inside a git checkout, so org.opencontainers.image.revision has no value" >&2
  exit 1
fi
build_date="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
remote_url="$(git remote get-url origin 2>/dev/null || true)"
remote_url="$(sed -E 's#^git@github.com:#https://github.com/#; s#\.git$##; s#^https://[^@/]+@#https://#' <<<"$remote_url")"
source_url="${SOURCE_URL:-$remote_url}"
source_url="${source_url:-https://github.com/gbadedata/dockerfile-data-validation}"

build_args=(
  --build-arg "VCS_REF=${vcs_ref}"
  --build-arg "BUILD_DATE=${build_date}"
  --build-arg "SOURCE_URL=${source_url}"
)
# dvgate prints KEY=VALUE pairs computed from the dataset, contracts and model.
while IFS= read -r pair; do
  build_args+=(--build-arg "$pair")
done < <(dvgate build-args \
           --record "$record" --contract "$data_contract" \
           --model-contract "$model_contract" --model-dir "$model_dir")

echo "building ${image} from ${record} with ${#build_args[@]} build args" >&2
docker build "${build_args[@]}" "$@" --tag "$image" .
