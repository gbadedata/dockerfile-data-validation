#!/usr/bin/env bash
# Verify a built image: labels against the standard and against the manifest
# inside the image, then run the container's own selfcheck.
#
# Usage: scripts/verify-image.sh IMAGE_TAG [REPORT_DIR]
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 IMAGE_TAG [REPORT_DIR]" >&2
  exit 64
fi
image="$1"
report_dir="${2:-$(mktemp -d)}"
mkdir -p "$report_dir"
cd "$(dirname "$0")/.."

docker image inspect "$image" --format '{{json .Config.Labels}}' > "${report_dir}/labels.json"
docker run --rm --entrypoint cat "$image" /app/reports/manifest.json > "${report_dir}/manifest.json"
for report in data.json model.json quarantine.csv; do
  docker run --rm --entrypoint cat "$image" "/app/reports/${report}" > "${report_dir}/${report}"
done

dvgate check-labels \
  --labels "${report_dir}/labels.json" \
  --manifest "${report_dir}/manifest.json" \
  --standard standards/labels.v1.yaml \
  --report "${report_dir}/labels-report.json"

# The default command re-hashes every shipped file against the manifest.
docker run --rm "$image"

user="$(docker image inspect "$image" --format '{{.Config.User}}')"
if [[ -z "$user" || "$user" == "0" || "$user" == root* ]]; then
  echo "FAIL: image runs as root (User='${user}')" >&2
  exit 2
fi
echo "verified ${image}: labels, manifest and selfcheck all pass; runs as ${user}"
