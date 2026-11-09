#!/usr/bin/env bash
# Run a command that must fail, and check it failed for the expected reason.
#
# Usage: scripts/expect-failure.sh 'regex the output must contain' -- command [args...]
# Passes only if the command exits non-zero AND its output matches the regex,
# so a negative test cannot pass because something unrelated broke.
set -uo pipefail

if [[ $# -lt 3 || "$2" != "--" ]]; then
  echo "usage: $0 'expected regex' -- command [args...]" >&2
  exit 64
fi
expected="$1"
shift 2

log="$(mktemp)"
trap 'rm -f "$log"' EXIT
"$@" >"$log" 2>&1
status=$?

if [[ $status -eq 0 ]]; then
  cat "$log"
  echo "FAIL: expected a failure, but the command succeeded" >&2
  exit 1
fi
if ! grep -Eq "$expected" "$log"; then
  tail -n 40 "$log"
  echo "FAIL: command failed (exit ${status}) but not with /${expected}/" >&2
  exit 1
fi
echo "ok: failed as expected (exit ${status}); matched /${expected}/:"
grep -E "$expected" "$log" | head -n 5 | sed 's/^/    /'
