#!/usr/bin/bash
set -euo pipefail

if [[ "$#" -ne 2 || "$1" != "--output-dir" || "$2" != /* ]]; then
  echo "usage: run-oci-correctness.sh --output-dir ABSOLUTE_NEW_DIRECTORY" >&2
  exit 64
fi
if [[ -e "$2" || -L "$2" ]]; then
  echo "OCI correctness evidence output must be a new path" >&2
  exit 73
fi
if [[ -v STACK_YAML || -v STACK_ROOT || -v STACK_OPTS || -v STACK_CONFIG ]]; then
  echo "ambient Stack configuration is forbidden" >&2
  exit 64
fi

: "${S1_4X_DOCKER_BIN:?absolute pinned Docker client is required}"
: "${S1_4X_DOCKER_SHA256:?pinned Docker client SHA-256 is required}"
[[ "$S1_4X_DOCKER_BIN" == /* \
  && -f "$S1_4X_DOCKER_BIN" \
  && -x "$S1_4X_DOCKER_BIN" \
  && ! -L "$S1_4X_DOCKER_BIN" \
  && "$(realpath "$S1_4X_DOCKER_BIN")" == "$S1_4X_DOCKER_BIN" \
  && "$(sha256sum "$S1_4X_DOCKER_BIN" | awk '{print $1}')" \
    == "$S1_4X_DOCKER_SHA256" ]] || {
  echo "Docker client identity mismatch" >&2
  exit 69
}

SCRIPT_PATH="$(readlink -f "$0")"
HASKELL_ROOT="$(realpath "${SCRIPT_PATH%/*}/..")"
"$HASKELL_ROOT/tools/assert-toolchain.sh" >/dev/null
"$HASKELL_ROOT/tools/select-proven-profile.sh" --check >/dev/null

# profile_workflow owns exact `docker build --network none` and
# `docker run --network none`; this outer wrapper never accepts arbitrary argv.
exec /usr/bin/python3 "$HASKELL_ROOT/tools/profile_workflow.py" \
  oci-correctness \
  --output-dir "$2"
